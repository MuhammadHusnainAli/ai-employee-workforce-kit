//! Types and gRPC stubs generated from `/contracts/proto`.
//!
//! Modules mirror the proto packages: `wfk.common.v1` is [`wfk::common::v1`].

#[allow(clippy::all, clippy::pedantic, missing_docs)]
pub mod wfk {
    pub mod common {
        pub mod v1 {
            tonic::include_proto!("wfk.common.v1");
        }
    }
}
