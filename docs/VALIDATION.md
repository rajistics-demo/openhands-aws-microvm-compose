# Validate the AWS pilot

Use an isolated Enterprise organization and a synthetic workspace. Keep raw
provider responses, screenshots, signed URLs, tokens and logs private.

1. Start a Canvas conversation with the AWS spec. Confirm a user message and
   completed tool action are visible in Enterprise history.
2. Copy [preview_probe.py](../validation/preview_probe.py) into the test
   workspace and run it on `0.0.0.0:8011`. Create a separate uncommitted
   workspace marker. Open the worker preview URL and click **Check live process**.
   Record the boot marker and counter.
3. With the agent idle, click **Pause** in the provider-aware Canvas build.
   On stock Canvas, use the Sandbox V1 pause route for a lifecycle-only run.
   Confirm AWS reports `SUSPENDED`. A preview read should return 409 without
   waking the VM. Verify Enterprise history is still readable.
4. Reopen the original conversation and time history visibility and a successful
   preview button response from that click. For an API-only lifecycle test,
   explicitly resume through Sandbox V1 and record that scope. Confirm the
   provider reports `RUNNING`; the marker, boot token and in-memory counter
   should survive. Check a second genuine workspace action.
5. Delete the disposable sandbox using Enterprise and verify AWS termination.
   Remove task-owned image versions, S3 artifacts, temporary role credentials,
   and other provisioned resources as appropriate. Restore the original Compose
   overlay list and remove only the AWS sidecar to return to the prior provider.
   Keep Postgres and application storage during rollback.

## Validation scope

Earlier isolated Compose pilots verified callbacks, native tool execution,
preview and lifecycle behavior, and 25- and 50-VM cohorts. The historical
Canvas Pause and expiry demonstrations used additional Canvas and recovery
changes. This package contains the standard Sandbox V1 path and the shared
backend Pause capability. Use the acceptance test above to qualify the
particular Canvas build, topology, credentials and guest image you choose.

Package checks verified that the patches apply to the Enterprise 1.88.0
release tree, the Compose overlays render with only the expected mounts,
and the image-builder request matches the AWS SDK model. The full
Enterprise regression suite and a new AWS rollout of this package remain
pending. The historical live trials used the earlier deployment files.
