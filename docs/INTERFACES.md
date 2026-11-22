# Interfaces and design choices

This is an experimental adapter for the same Enterprise managed sandbox
interface used by E2B and DigitalOcean MARS. Provider experiments can replace
an individual choice below while keeping the observable Canvas behavior stable.

| Interface | AWS choice and reason | Changing the choice |
|---|---|---|
| Sandbox spec | `LambdaMicrovmSpecServiceInjector` resolves `lambda-agent-server` to a pinned image ARN, active version and bootstrap key. Exact versioning makes results reproducible. Provider configuration and keys stay out of the API spec response. | An allowlisted multi-image catalog can map workload profiles to versions, CPUs and memory. Validate selection and scope the AWS role to each allowed image. |
| Ownership and launch | `LambdaMicrovmService` uses the existing managed sandbox store and records the AWS VM ID under the current user and credential organization. The ID is separate from the conversation ID. The adapter verifies provider state and terminates failed starts with a known VM ID. | Keep the same ownership checks. A durable create intent and external reconciler can account for a response lost before the VM ID reaches Enterprise. The pilot has no independent reconciler for that case. |
| Guest startup | The image snapshots a dormant Agent Server. The AWS `ready` hook checks `/api/init`; the controller initializes it after launch with per-sandbox session and encryption keys, working paths and webhook configuration. That makes a single guest image reusable across independent conversations. | Use deferred init or another authenticated bootstrap with equivalent isolation. Keep AWS control credentials outside the image and guest. |
| HTTP and WebSocket | `lambda_microvm_proxy` resolves only Enterprise-owned VM IDs, validates the AWS endpoint host, and uses port-scoped provider tokens. Agent Server authenticates its own traffic at 8000. 8001 is reserved for the editor. Ports 8011 and 8012 serve workspace previews. | A different bridge can terminate traffic or resolve destinations another way if it preserves session auth, streaming, WebSocket reconnect, and a separate preview origin. The pilot's preview trust boundary is the operator network. |
| Callback and history | The guest posts conversation metadata and events to a scoped HTTPS callback origin. Enterprise validates the bound Agent Server session key and stores conversation history, which can be read while the VM is suspended. | A private network or a dedicated relay can replace the public TLS callback path. Confirm that a real message and tool event reach Enterprise and remain visible during suspension. |
| Pause and reopen | The service waits for the AWS `SUSPENDED` and `RUNNING` states and disables AWS auto-resume. A status or preview GET must leave a paused VM paused. Enterprise exposes the Pause capability with the existing sandbox and conversation models. | Changes can preserve the VM or replace it. State which process and workspace data survives. Test with a counter held in memory and an uncommitted file. |
| Expiry | Lambda counts time running and suspended toward its configured VM lifetime. This package reports a terminated VM as missing. Earlier pilot code recovered files and history after expiry with an additional workspace checkpoint service. | Save a verified workspace checkpoint before termination and update the original conversation binding only after restore succeeds. Choose storage, retention, credentials, and preview restart semantics explicitly. |
| Admission and cleanup | The pilot caps concurrent active sandboxes, keeps one conversation per VM, and uses a short provider lifetime. Delete requests target known owned VM IDs. The AWS lifetime bounds billable orphan risk. | Choose per-org budgets and a reconciliation policy appropriate to your workload. Preserve ownership and report cleanup failures without deleting unrelated resources. |

## API contract

Use an authenticated API session in the intended Enterprise organization.

| Operator action | Enterprise route | Observe |
|---|---|---|
| Allocate | `POST /api/v1/sandboxes?sandbox_spec_id=lambda-agent-server` | Correct spec and owner, `RUNNING`, port bindings and Pause capability |
| Create a conversation | `POST /api/v1/app-conversations` with `sandbox_id`; poll `GET /api/v1/app-conversations/start-tasks?ids=<task-id>` | `READY` and expected sandbox ID |
| Pause or resume | `POST /api/v1/sandboxes/<id>/pause` or `/resume` | Read `GET /api/v1/sandboxes?id=<id>` until the provider-backed state settles |
| Read history | `GET /api/v1/conversation/<id>/events/search` | Original message and tool events, including while paused |
| Delete | `DELETE /api/v1/sandboxes/<id>` | Enterprise removes the row and AWS confirms termination |

Use the same acceptance criteria across provider changes. Record the chosen
base image and version, VM size, API/SDK version, release versions, same-process
or replacement semantics, reopen-to-history and reopen-to-preview latency,
and outcomes after a missing or timed-out provider response.
