# Token Garden architecture decisions

## Separate Python assembly from Shaka repository checks

Shaka's project schema cannot represent Python, and its build command is a stub. Token Garden therefore has no `project.cue`. Python recipes perform actual assembly, lint, typing, tests, and independent application/release-tool coverage gates; Shaka runs repository preflight and whitespace checks.

This keeps local and CI gates shared without misclassifying the app or claiming an empty project loop validates Python. It costs a handwritten workflow, explicit workflow accounting, and colocated Jujutsu metadata for Shaka's tracked-file listing. Tool and dependency lockfiles pin the integration.

## Release only reviewed files

The app's working directory may contain live state, environments, or generated artifacts. Setuptools uses an allowlisted source manifest; a separate exact archive inventory rejects unexpected release members. The wheel contains only application modules and distribution metadata. The source archive also carries synthetic tests and development inputs.

The inventory must be deliberately updated when release files change. That maintenance cost is preferable to accidentally distributing private state. Licensing and publication remain separate owner decisions.
