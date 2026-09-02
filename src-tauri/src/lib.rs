//! M7: Tauri 壳入口——sidecar 守护 + 全局快捷键 + 前后端命令桥。

mod hotkey;
mod sidecar;

use serde::Deserialize;

#[derive(Deserialize)]
struct StartArgs {
    profile_id: String,
    replay_path: Option<String>,
    replay_speed: Option<f64>,
}

/// 前端命令：开始监听（选择档案后调用，F3/F8）。
#[tauri::command]
async fn start_listening(args: StartArgs) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(sidecar::sidecar_url("/sessions"))
        .json(&serde_json::json!({
            "profile_id": args.profile_id,
            "replay_path": args.replay_path,
            "replay_speed": args.replay_speed,
        }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let body: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    if let Some(sid) = body.get("session_id").and_then(|v| v.as_str()) {
        *hotkey::ACTIVE_SESSION.lock().unwrap() = Some(sid.to_string());
    }
    Ok(body)
}

/// 前端命令：停止监听。
#[tauri::command]
async fn stop_listening() -> Result<(), String> {
    let sid = hotkey::ACTIVE_SESSION.lock().unwrap().take();
    if let Some(sid) = sid {
        let _ = reqwest::Client::new()
            .post(sidecar::sidecar_url(&format!("/sessions/{}/stop", sid)))
            .send()
            .await;
    }
    Ok(())
}

/// 前端命令：sidecar 健康状态（N8 状态展示）。
#[tauri::command]
async fn sidecar_health() -> bool {
    reqwest::Client::new()
        .get(sidecar::sidecar_url("/health"))
        .timeout(std::time::Duration::from_millis(800))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    sidecar::start_guardian();

    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            start_listening,
            stop_listening,
            sidecar_health,
        ])
        .setup(|app| {
            hotkey::register(app.handle())
                .map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running EchoPilot");
}
