//! M7: 全局快捷键（N7）——焦点在任何应用时可用。
//! Cmd/Ctrl+Shift+R 重新生成；Cmd/Ctrl+Shift+S 循环切换档位；
//! Cmd/Ctrl+Shift+X 停止监听。启动监听在主窗口选择档案后触发。

use std::sync::Mutex;

use once_cell::sync::Lazy;
use tauri::AppHandle;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

use crate::sidecar::sidecar_url;

/// 当前运行中的会话（由前端 start_listening 命令设置）。
pub static ACTIVE_SESSION: Lazy<Mutex<Option<String>>> =
    Lazy::new(|| Mutex::new(None));

static STYLES: [&str; 3] = ["concise", "standard", "detailed"];
static CURRENT_STYLE: Lazy<Mutex<usize>> = Lazy::new(|| Mutex::new(1));

fn post(path: String, body: serde_json::Value) {
    std::thread::spawn(move || {
        if let Ok(client) = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
        {
            let _ = client.post(sidecar_url(&path)).json(&body).send();
        }
    });
}

fn with_session<F: Fn(String) + Send + 'static>(f: F) {
    let sid = ACTIVE_SESSION.lock().unwrap().clone();
    if let Some(sid) = sid {
        f(sid);
    }
}

pub fn register(app: &AppHandle) -> Result<(), String> {
    app.global_shortcut().on_shortcut(
        "CmdOrControl+Shift+R",
        move |_app, _shortcut, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            with_session(|sid| {
                post(format!("/sessions/{}/trigger", sid),
                     serde_json::json!({"mode": "regen"}));
            });
        },
    ).map_err(|e| e.to_string())?;

    app.global_shortcut().on_shortcut(
        "CmdOrControl+Shift+S",
        move |_app, _shortcut, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            let mut idx = CURRENT_STYLE.lock().unwrap();
            *idx = (*idx + 1) % STYLES.len();
            let style = STYLES[*idx];
            drop(idx);
            with_session(move |sid| {
                post(format!("/sessions/{}/style", sid),
                     serde_json::json!({"style": style}));
            });
        },
    ).map_err(|e| e.to_string())?;

    app.global_shortcut().on_shortcut(
        "CmdOrControl+Shift+X",
        move |_app, _shortcut, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            with_session(|sid| {
                post(format!("/sessions/{}/stop", sid),
                     serde_json::json!({}));
            });
            *ACTIVE_SESSION.lock().unwrap() = None;
        },
    ).map_err(|e| e.to_string())?;
    Ok(())
}
