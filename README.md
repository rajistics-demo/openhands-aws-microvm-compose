# AWS Lambda MicroVMs for OpenHands Enterprise

**Experimental.** This package runs OpenHands Agent Server in AWS Lambda
MicroVMs. Enterprise, Canvas, Postgres, Redis and the runtime gateway run in
Docker Compose. It targets Enterprise **1.88.0**, Agent Server **1.60.1-python**
and Canvas **1.35.0**.

- [Install the adapter and launch a MicroVM](docs/SETUP.md)
- [Interfaces, design choices, and ways to change them](docs/INTERFACES.md)
- [Build the pinned Canvas Pause demo](canvas/README.md)
- [Validate Pause and reopen, and clean up](docs/VALIDATION.md)

The package includes the [Enterprise provider modules](enterprise/overlay/),
[Compose overlays](compose/), [guest image and hooks](guest/), [image builder](scripts/build_image.py),
and [IAM policy examples](iam/). Build the derived control-plane image from
your licensed Enterprise image. The existing Canvas client uses Sandbox V1.
Showing the dedicated Pause button requires the provider-aware Canvas build.
The earlier AWS demos also used separate checkpoint and expiry-recovery
changes.

Earlier isolated pilots exercised native conversation starts, pause and resume,
working previews, and cohorts of 25 and 50 VMs. This installer is packaged
from that adapter. Its validation and differences from those demos are in
the [test guide](docs/VALIDATION.md) and [provider status record](provider.json).

## Sandbox Spec compatibility

The AWS adapter uses the Enterprise Sandbox Spec catalog. Its
`lambda-agent-server` spec maps to the configured AWS Lambda MicroVM image
and pinned version.

Enterprise’s Sandbox Spec selector and Agent Profile references use the
same ID contract when those features are available in the Enterprise
release. Kubernetes uses that contract to refer to a named runtime
configuration. This Compose package activates a single sandbox backend.
Offering Kubernetes and microVMs in one installation still requires
routing new launches to the selected provider and routing existing
conversations through their saved sandbox binding.
