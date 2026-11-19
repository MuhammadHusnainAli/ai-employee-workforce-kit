// gRPC clients for the services behind the public API, from this app's copies of their contracts
// (contracts/proto). Server-side only.
//
// Every call has a deadline. Messages use camelCase fields, enums as their names
// ("TASK_STATUS_QUEUED"), 64-bit integers as strings and Timestamps as {seconds, nanos}.

import path from "node:path";
import * as grpc from "@grpc/grpc-js";
import * as loader from "@grpc/proto-loader";

export const DEADLINE_MS = 5_000;

const PROTO_ROOT = path.join(process.cwd(), "contracts", "proto");
const FILES = [
    "wfk/task_service/v1/tasks.proto",
    "wfk/agent_runtime/v1/runs.proto",
    "wfk/brain_service/v1/brain.proto",
    "wfk/workforce_service/v1/workforce.proto",
];

type Unary = (
    req: object,
    metadata: grpc.Metadata,
    options: grpc.CallOptions,
    cb: (err: grpc.ServiceError | null, res: unknown) => void,
) => void;
type ClientCtor = new (address: string, creds: grpc.ChannelCredentials) => grpc.Client;

let loaded: grpc.GrpcObject | undefined;

function packages(): grpc.GrpcObject {
    if (!loaded) {
        const def = loader.loadSync(FILES, {
            includeDirs: [PROTO_ROOT],
            keepCase: false,
            longs: String,
            enums: String,
            defaults: true,
            oneofs: true,
        });
        loaded = grpc.loadPackageDefinition(def);
    }
    return loaded;
}

function ctor(dotted: string): ClientCtor {
    let node: unknown = packages();
    for (const part of dotted.split(".")) {
        node = (node as Record<string, unknown>)[part];
    }
    if (typeof node !== "function") throw new Error(`no gRPC service ${dotted} in the contracts`);
    return node as ClientCtor;
}

/** A typed-enough unary caller: call("CreateTask", {...}) -> response object. */
export interface Service {
    call<T = Record<string, unknown>>(method: string, request: object): Promise<T>;
}

export function service(dotted: string, address: string): Service {
    const client = new (ctor(dotted))(address, grpc.credentials.createInsecure());
    return {
        call<T>(method: string, request: object): Promise<T> {
            const fn = (client as unknown as Record<string, Unary>)[method];
            if (typeof fn !== "function") {
                return Promise.reject(new Error(`${dotted} has no method ${method}`));
            }
            return new Promise<T>((resolve, reject) => {
                fn.call(
                    client,
                    request,
                    new grpc.Metadata(),
                    { deadline: Date.now() + DEADLINE_MS },
                    (err, res) => (err ? reject(err) : resolve(res as T)),
                );
            });
        },
    };
}

/** The backends the API talks to. Tests pass fakes. */
export interface Backends {
    tasks: Service;
    runs: Service;
    brain: Service;
    workforce: Service;
}

let backends: Backends | undefined;

export function realBackends(): Backends {
    if (!backends) {
        const env = process.env;
        backends = {
            tasks: service(
                "wfk.task_service.v1.TaskService",
                env.WFK_TASK_SERVICE_ADDR ?? "127.0.0.1:7002",
            ),
            runs: service(
                "wfk.agent_runtime.v1.RunService",
                env.WFK_RUN_SERVICE_ADDR ?? "127.0.0.1:7001",
            ),
            brain: service(
                "wfk.brain_service.v1.BrainService",
                env.WFK_BRAIN_SERVICE_ADDR ?? "127.0.0.1:7001",
            ),
            workforce: service(
                "wfk.workforce_service.v1.WorkforceService",
                env.WFK_WORKFORCE_SERVICE_ADDR ?? "127.0.0.1:7001",
            ),
        };
    }
    return backends;
}

export { grpc };
