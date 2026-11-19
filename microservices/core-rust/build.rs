//! Generates Rust code for every .proto file under this service's contracts/proto.

use std::path::{Path, PathBuf};

fn protos(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(dir).expect("read proto dir") {
        let path = entry.expect("dir entry").path();
        if path.is_dir() {
            protos(&path, out);
        } else if path.extension().is_some_and(|e| e == "proto") {
            out.push(path);
        }
    }
}

fn main() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contracts/proto");
    // Not canonicalized: on Windows that adds a `\?\` prefix protoc cannot read.
    assert!(
        root.is_dir(),
        "contracts/proto not found at {}",
        root.display()
    );
    let mut files = Vec::new();
    protos(&root, &mut files);
    files.sort();

    // A pinned protoc ships with the build, so no system install is needed.
    let mut config = prost_build::Config::new();
    config.protoc_executable(protoc_bin_vendored::protoc_bin_path().expect("vendored protoc"));

    println!("cargo:rerun-if-changed={}", root.display());
    tonic_prost_build::configure()
        .build_server(true)
        .build_client(true)
        .compile_with_config(config, &files, &[root])
        .expect("compile protos");
}
