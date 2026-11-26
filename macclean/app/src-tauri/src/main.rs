#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

fn engine_path() -> PathBuf {
    if let Ok(custom) = std::env::var("MACCLEAN_ENGINE") {
        return PathBuf::from(custom);
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("../Resources/engine/api.py"));
            candidates.push(dir.join("engine/api.py"));
        }
    }
    candidates.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("engine/api.py"));
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("engine/api.py"));
        candidates.push(cwd.join("src-tauri/engine/api.py"));
    }
    for candidate in &candidates {
        if candidate.exists() {
            return candidate.canonicalize().unwrap_or_else(|_| candidate.clone());
        }
    }
    PathBuf::from("engine/api.py")
}

fn python_bin() -> String {
    if let Ok(custom) = std::env::var("MACCLEAN_PYTHON") {
        return custom;
    }
    for path in [
        "/usr/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
    ] {
        if Path::new(path).exists() {
            return path.to_string();
        }
    }
    "python3".to_string()
}

fn call(args: Vec<String>) -> Result<Value, String> {
    let output = Command::new(python_bin())
        .arg(engine_path())
        .args(&args)
        .output()
        .map_err(|err| format!("无法启动 Python：{err}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if stdout.is_empty() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            "引擎没有返回内容".to_string()
        } else {
            stderr
        });
    }
    if let Ok(value) = serde_json::from_str::<Value>(&stdout) {
        if value.get("ok").and_then(|v| v.as_bool()) == Some(false) {
            return Err(value
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("引擎返回失败")
                .to_string());
        }
        return Ok(value);
    }
    Err(format!(
        "无法解析引擎返回：{}",
        stdout.chars().take(200).collect::<String>()
    ))
}

async fn run_blocking<F, T>(task: F) -> Result<T, String>
where
    F: FnOnce() -> Result<T, String> + Send + 'static,
    T: Send + 'static,
{
    tauri::async_runtime::spawn_blocking(task)
        .await
        .map_err(|err| err.to_string())?
}

#[tauri::command]
async fn scan(home: Option<String>, budget: Option<f64>) -> Result<Value, String> {
    run_blocking(move || {
        let mut args = vec!["scan".to_string()];
        if let Some(home) = home.filter(|h| !h.is_empty()) {
            args.push("--home".into());
            args.push(home);
        }
        args.push("--budget".into());
        args.push(format!("{}", budget.unwrap_or(90.0)));
        call(args)
    })
    .await
}

#[tauri::command]
async fn clean_plan(levels: Option<Vec<String>>) -> Result<Value, String> {
    run_blocking(move || {
        let mut args = vec!["plan-clean".to_string(), "--levels".into()];
        for level in levels.unwrap_or_else(|| vec!["S".into()]) {
            args.push(level);
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn clean_run(paths: Vec<String>) -> Result<Value, String> {
    if paths.is_empty() {
        return Err("没有选中任何项目".into());
    }
    run_blocking(move || {
        let mut args = vec!["run-clean".to_string()];
        for path in paths {
            args.push("--path".into());
            args.push(path);
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn apps(enrich: Option<bool>, fresh: Option<bool>) -> Result<Value, String> {
    run_blocking(move || {
        let mut args = vec!["apps".to_string()];
        if enrich.unwrap_or(false) {
            args.push("--sizes".into());
            args.push("--meta".into());
        }
        if fresh.unwrap_or(false) {
            args.push("--fresh".into());
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn uninstall_plan(app: String) -> Result<Value, String> {
    run_blocking(move || call(vec!["plan-uninstall".into(), app])).await
}

#[tauri::command]
async fn uninstall_run(app: String, with_data: bool, include_probable: bool) -> Result<Value, String> {
    run_blocking(move || {
        let mut args = vec!["run-uninstall".into(), app];
        if with_data {
            args.push("--with-data".into());
        }
        if include_probable {
            args.push("--include-probable".into());
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn inspect_container(path: String) -> Result<Value, String> {
    run_blocking(move || call(vec!["container".into(), "--path".into(), path])).await
}

#[tauri::command]
async fn delete_subpaths(root: String, paths: Vec<String>) -> Result<Value, String> {
    run_blocking(move || {
        if paths.is_empty() {
            return Err("没有选中任何子项".into());
        }
        let mut args = vec!["container-delete".into(), "--root".into(), root];
        for path in paths {
            args.push("--path".into());
            args.push(path);
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn delete_paths(app: String, paths: Vec<String>) -> Result<Value, String> {
    run_blocking(move || {
        if paths.is_empty() {
            return Err("没有选中任何项目".into());
        }
        let mut args = vec!["delete-paths".into(), app];
        for path in paths {
            args.push("--path".into());
            args.push(path);
        }
        call(args)
    })
    .await
}

#[tauri::command]
async fn usage(action: String) -> Result<Value, String> {
    run_blocking(move || call(vec!["usage".into(), action])).await
}

#[tauri::command]
async fn doctor() -> Result<Value, String> {
    run_blocking(move || call(vec!["doctor".to_string()])).await
}

#[tauri::command]
fn disk_info() -> Result<Value, String> {
    let output = Command::new("df")
        .arg("-k")
        .arg("/")
        .output()
        .map_err(|err| format!("无法读取磁盘信息：{err}"))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let line = text
        .lines()
        .nth(1)
        .ok_or_else(|| "df 输出为空".to_string())?;
    let cols: Vec<&str> = line.split_whitespace().collect();
    if cols.len() < 4 {
        return Err("df 输出格式不认识".into());
    }
    let to_bytes = |s: &str| s.parse::<u64>().unwrap_or(0) * 1024;
    Ok(json!({
        "total": to_bytes(cols[1]),
        "used": to_bytes(cols[2]),
        "free": to_bytes(cols[3]),
    }))
}

#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    if !Path::new(&path).exists() {
        return Err("路径不存在".into());
    }
    Command::new("open")
        .arg("-R")
        .arg(&path)
        .spawn()
        .map(|_| ())
        .map_err(|err| err.to_string())
}

#[tauri::command]
fn open_trash() -> Result<(), String> {
    Command::new("open")
        .arg(format!("{}/.Trash", std::env::var("HOME").unwrap_or_default()))
        .spawn()
        .map(|_| ())
        .map_err(|err| err.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            scan,
            clean_plan,
            clean_run,
            apps,
            uninstall_plan,
            uninstall_run,
            delete_paths,
            inspect_container,
            delete_subpaths,
            doctor,
            usage,
            disk_info,
            reveal,
            open_trash
        ])
        .run(tauri::generate_context!())
        .expect("macclean 启动失败");
}
