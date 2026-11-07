// Previene una finestra di console extra su Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    atlas_desktop_lib::run()
}
