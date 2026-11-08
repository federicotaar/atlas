//! ATLAS desktop: avvia il backend Python come sidecar e lo ferma all'uscita.
//!
//! Regole porte (requisito di progetto: mai toccare server che non avviamo noi):
//! - se 127.0.0.1:8000 risponde gia' come istanza ATLAS COMPATIBILE
//!   (app="atlas", api="v1", versione >= MIN_REUSE_VERSION), la RIUSIAMO e
//!   non la tocchiamo mai;
//! - un vecchio backend (es. Docker 2.0.x senza campo version) NON viene
//!   riusato: gli mancherebbero auth locale, migrazione e connettori. Non lo
//!   uccidiamo e non lo tocchiamo: il sidecar parte su una porta di riserva
//!   nostra e l'app usa quella;
//! - stessa cura per un servizio estraneo su 8000: mai uccidere nulla di non
//!   nostro, si passa alla porta di riserva;
//! - chiudiamo solo il processo che abbiamo avviato noi.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;

const PORT_CANDIDATES: [u16; 10] = [8000, 8100, 8101, 8102, 8103, 8104, 8105, 8106, 8107, 8108];
/// Versione minima di un backend esistente per essere riusata: la 2.1.1
/// aggiunge auth locale, migrazione guidata e connettori WhatsApp/Telegram;
/// un backend piu' vecchio non li avrebbe e l'app risulterebbe rotta.
const MIN_REUSE_VERSION: (u32, u32, u32) = (2, 1, 1);

struct Sidecar(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

#[derive(PartialEq, Debug)]
enum Probe {
    /// Istanza ATLAS con versione compatibile: riusabile.
    AtlasCompatible,
    /// Istanza ATLAS troppo vecchia (senza version o sotto il minimo).
    AtlasOutdated,
    /// Qualcos'altro occupa la porta.
    Foreign,
    /// Nessuno in ascolto.
    Nothing,
}

fn parse_version(v: &str) -> Option<(u32, u32, u32)> {
    let mut parts = v.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch = parts.next()?.parse().ok()?;
    Some((major, minor, patch))
}

fn probe(port: u16) -> Probe {
    let url = format!("http://127.0.0.1:{port}/health");
    let resp = match ureq::get(&url).timeout(Duration::from_millis(800)).call() {
        Ok(r) => r,
        // Qualcuno risponde con un errore HTTP: la porta e' occupata, non nostra.
        Err(ureq::Error::Status(_, r)) => r,
        // Connessione rifiutata o timeout: nessuno in ascolto.
        Err(_) => return Probe::Nothing,
    };
    if resp.status() != 200 {
        return Probe::Foreign;
    }
    let Ok(v) = resp.into_json::<serde_json::Value>() else {
        return Probe::Foreign;
    };
    let is_atlas = v.get("app").and_then(|a| a.as_str()) == Some("atlas")
        && v.get("api").and_then(|a| a.as_str()) == Some("v1");
    if !is_atlas {
        return Probe::Foreign;
    }
    match v.get("version").and_then(|x| x.as_str()).and_then(parse_version) {
        Some(ver) if ver >= MIN_REUSE_VERSION => Probe::AtlasCompatible,
        _ => Probe::AtlasOutdated,
    }
}

fn wait_health(port: u16) -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(30) {
        if probe(port) == Probe::AtlasCompatible {
            return true;
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // 1) Un'istanza compatibile gia' attiva (avviata dall'utente o da
            //    un altro avvio dell'app) viene riusata: niente da chiudere.
            for port in PORT_CANDIDATES {
                if probe(port) == Probe::AtlasCompatible {
                    app.manage(Sidecar(Mutex::new(None)));
                    return Ok(());
                }
            }

            // 2) Prima porta libera tra le candidate nostre. Vecchi backend e
            //    servizi estranei restano vivi e intoccati.
            let free_port = PORT_CANDIDATES
                .iter()
                .copied()
                .find(|&p| probe(p) == Probe::Nothing);
            let Some(port) = free_port else {
                app.handle().emit(
                    "backend-port-busy",
                    "Nessuna porta libera tra 8000 e 8108: chiudi un'istanza di ATLAS o il servizio che le occupa",
                )?;
                app.manage(Sidecar(Mutex::new(None)));
                return Ok(());
            };
            if port != 8000 {
                eprintln!(
                    "[atlas] 127.0.0.1:8000 occupata (vecchia versione o altro servizio): sidecar su porta {port}"
                );
            }

            let data_dir = app
                .path()
                .app_data_dir()
                .expect("app data dir non disponibile");
            std::fs::create_dir_all(&data_dir).expect("impossibile creare app data dir");

            let sidecar = app
                .shell()
                .sidecar("atlas-backend")
                .expect("sidecar atlas-backend non trovato nel bundle")
                .env("ATLAS_DESKTOP_DATA_DIR", &data_dir)
                .env("ATLAS_PORT", port.to_string())
                // Il backend si auto-termina se questo processo non esiste piu'.
                .env("ATLAS_APP_PID", std::process::id().to_string());
            let (mut rx, child) = sidecar.spawn().expect("avvio sidecar fallito");
            app.manage(Sidecar(Mutex::new(Some(child))));
            // Drena stdout/stderr del sidecar: errori di avvio visibili, mai morte silenziosa.
            std::thread::spawn(move || {
                while let Some(event) = rx.blocking_recv() {
                    use tauri_plugin_shell::process::CommandEvent;
                    match event {
                        CommandEvent::Stdout(line) => {
                            eprintln!("[sidecar] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[sidecar:err] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Terminated(status) => {
                            eprintln!("[sidecar] terminato: {:?}", status);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if !wait_health(port) {
                    eprintln!("ATLAS backend non risponde su 127.0.0.1:{port} entro 30s");
                    let _ = handle.emit("backend-failed", ());
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Solo il processo avviato da noi; un'istanza riusata resta viva.
                let state = window.state::<Sidecar>();
                let child = state.0.lock().ok().and_then(|mut g| g.take());
                if let Some(child) = child {
                    let pid = child.pid();
                    eprintln!("[atlas] chiusura finestra: SIGTERM al bootloader pid {pid}");
                    // Prima SIGTERM: il bootloader PyInstaller lo inoltra al
                    // server Python figlio (SIGKILL non e' inoltrabile). Solo
                    // sul processo avviato da noi; un'istanza riusata non e'
                    // mai nello state e resta intoccata.
                    #[cfg(target_os = "windows")]
                    {
                        let _ = std::process::Command::new("taskkill")
                            .args(["/T", "/F", "/PID", &pid.to_string()])
                            .status();
                    }
                    #[cfg(not(target_os = "windows"))]
                    {
                        let _ = std::process::Command::new("kill")
                            .args(["-TERM", &pid.to_string()])
                            .status();
                        std::thread::sleep(std::time::Duration::from_millis(800));
                    }
                    let r = child.kill();
                    eprintln!("[atlas] chiusura finestra: kill bootloader -> {r:?}");
                } else {
                    eprintln!("[atlas] chiusura finestra: nessun sidecar avviato da noi");
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ATLAS desktop");
}
