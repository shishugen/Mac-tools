fn main() {
    // Tauri 默认不会监听 frontendDist 的变化，改前端后 cargo build 不会重打包资源。
    // 显式监听 ../src，保证 index.html / app.js 改动能进入新二进制。
    println!("cargo:rerun-if-changed=../src/index.html");
    println!("cargo:rerun-if-changed=../src/app.js");
    tauri_build::build()
}
