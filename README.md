# AWS Lambda MicroVMs for OpenHands Enterprise

This is the provider-facing pilot package. The Enterprise implementation is owned by the existing Enterprise checkout. The shared [provider contract](../openhands-provider-contract/CONTRACT.md) and [Canvas acceptance test](../openhands-provider-contract/CANVAS.md) define the behavior to verify.

| Seam | AWS pilot mapping |
|---|---|
| Image and resources | The native spec resolves an immutable Lambda image version. The mixed workload proof used three x86 images at a 2 GiB baseline. |
| Lifecycle and recovery | The Enterprise managed service owns the sandbox record and calls Lambda MicroVM lifecycle APIs. The pilot restored the original conversation and Git workspace into a replacement VM after expiry. |
| Guest identity | The Agent Server starts dormant and receives sandbox-scoped credentials and callback config at init. AWS control-plane credentials stay on the Compose host. |
| HTTP, WebSocket and preview | The runtime gateway uses provider-scoped endpoint tokens. Canvas verifies the same preview process after resume. Reconnect behavior is an explicit test. |
| Events and failure handling | Authenticated callbacks populate Enterprise history. Restrict callback ingress to the runtime network and verify saved tool records. Record ambiguous creates for reconciliation. |
| Cleanup | Terminate only owned VMs and remove task-owned images, credentials and host resources. |

The verified isolated pilot used Enterprise 1.88.0, Canvas 1.35.0, and Agent Server 1.60.1. It passed 25 and 50 concurrent workload runs. Pilot overlays include the Canvas Pause button and expiry recovery. Sharing the normal API and publishing those changes through the owning product repos are independent steps.

Run the [shared API smoke](../openhands-provider-contract/README.md) with this repo's `provider.json` and a selected AWS spec. Then run the Canvas acceptance case using the configured live preview. Review [AWS image and sizing](https://docs.aws.amazon.com/lambda/latest/dg/microvms-images.html) and [AWS lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/microvms-launching.html) for alternate designs.
