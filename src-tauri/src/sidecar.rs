//! M7: Tauri 壳——sidecar 进程守护（N3/N8）。
//! spawn sidecar（uv run uvicorn … --factory），轮询 /health，
//! 崩溃或健康检查连续失败时自动重启。

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

pub const SIDECAR_PORT: u16 = 18321;

static GUARDIAN: Mutex<Option<Guardian>> = Mutex::new(None);

struct Guardian {
    child: Option<Child>,
    sidecar_dir: PathBuf,
    consecutive_failures: u32,
}

pub fn sidecar_url(path: &str) -> String {
    format!("http://127.0.0.1:{}{}", SIDECAR_PORT, path)
}

fn spawn_child(sidecar_dir: &PathBuf) -> std::io::Result<Child> {
    Command::new("uv")
        .args([
            "run", "uvicorn", "sidecar.main:create_app",
            "--factory", "--port", &SIDECAR_PORT.to_string(),
        ])
        .current_dir(sidecar_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
}

fn health_ok() -> bool {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(800))
        .build()
        .ok()
        .and_then(|c| c.get(sidecar_url("/health")).send().ok())
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// 启动守护线程：确保 sidecar 始终存活且健康。
pub fn start_guardian() {
    let sidecar_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../sidecar");
    {
        let mut g = GUARDIAN.lock().unwrap();
        *g = Some(Guardian {
            child: spawn_child(&sidecar_dir).ok(),
            sidecar_dir,
            consecutive_failures: 0,
        });
    }
    std::thread::spawn(|| loop {
        std::thread::sleep(Duration::from_secs(2));
        let mut g = GUARDIAN.lock().unwrap();
        let Some(guard) = g.as_mut() else { continue };

        let exited = guard
            .child
            .as_mut()
            .and_then(|c| c.try_wait().ok().flatten())
            .is_some();

        if exited {
            guard.child = spawn_child(&guard.sidecar_dir).ok();
            guard.consecutive_failures = 0;
            continue;
        }

        if health_ok() {
            guard.consecutive_failures = 0;
        } else {
            guard.consecutive_failures += 1;
            // 进程还在但连续 3 次（≈6s）不健康 → 杀掉重启
            if guard.consecutive_failures >= 3 {
                if let Some(mut c) = guard.child.take() {
                    let _ = c.kill();
                }
                guard.child = spawn_child(&guard.sidecar_dir).ok();
                guard.consecutive_failures = 0;
            }
        }
    });
}

/// 等待 sidecar 就绪（供启动时阻塞调用，最长 timeout_ms）。
pub fn wait_ready(timeout_ms: u64) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed().as_millis() < timeout_ms as u128 {
        if health_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}
