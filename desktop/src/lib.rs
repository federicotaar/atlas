//! ATLAS desktop: avvia il backend Python come sidecar e lo ferma all'uscita.
//!
//! Regole porta 8000 (decise con il proprietario):
//! - se 127.0.0.1:8000 risponde gia' come istanza ATLAS compatibile
//!   (app="atlas", api="v1"), la RIUSIAMO e non la tocchiamo mai;
//! - se la porta e' occupata da qualcos'altro, NON uccidiamo nulla: stato
//!   errore "porta occupata", l'utente decide;
//! - chiudiamo solo il processo che abbiamo avviato noi.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;

const HEALTH_URL: &str = "http://127.0.0.1:8000/health";

struct Sidecar(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

#[derive(PartialEq)]
enum Probe {
    AtlasCompatible,
    Foreign,
    Nothing,
}

fn probe() -> Probe {
    let Ok(resp) = ureq::get(HEALTH_URL)
        .timeout(Duration::from_millis(800))
        .call()
    else {
        return Probe::Nothing;
    };
    if resp.status() != 200 {
        return Probe::Foreign;
    }
    match resp.into_json::<serde_json::Value>() {
        Ok(v) if v.get("app").and_then(|a| a.as_str()) == Some("atlas")
            && v.get("api").and_then(|a| a.as_str()) == Some("v1") =>
        {
            Probe::AtlasCompatible
        }
        _ => Probe::Foreign,
    }
}

fn wait_health() -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(30) {
        if probe() == Probe::AtlasCompatible {
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
            match probe() {
                Probe::AtlasCompatible => {
                    // Istanza ATLAS gia' attiva (avviata dall'utente o da un
                    // altro avvio dell'app): la riusiamo. Niente da chiudere.
                    app.manage(Sidecar(Mutex::new(None)));
                    return Ok(());
                }
                Probe::Foreign => {
                    // Mai uccidere un server che non e' nostro.
                    app.handle().emit(
                        "backend-port-busy",
                        "127.0.0.1:8000 e' occupata da un servizio che non e' ATLAS",
                    )?;
                    app.manage(Sidecar(Mutex::new(None)));
                    return Ok(());
                }
                Probe::Nothing => {}
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
                .env("ATLAS_DESKTOP_DATA_DIR", &data_dir);
            let (_rx, child) = sidecar.spawn().expect("avvio sidecar fallito");
            app.manage(Sidecar(Mutex::new(Some(child))));

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if !wait_health() {
                    eprintln!("ATLAS backend non risponde su 127.0.0.1:8000 entro 30s");
                    let _ = handle.emit("backend-failed", ());
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Solo il processo avviato da noi; un'istanza riusata resta viva.
                let state = window.state::<Sidecar>();
                let child = state.0.lock().unwrap().take();
                if let Some(child) = child {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ATLAS desktop");
}
