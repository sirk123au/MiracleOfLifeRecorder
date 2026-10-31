# Release notes

## 1.0.43

- Added manual update checking and verified installer downloads from GitHub Releases in Settings.
- Kept updates separate from installation: finish work and exit from the tray before running the installer. Settings, credentials, patient history and USB media are preserved.
- Patient albums now start collapsed and can be expanded individually.
- Added full-resolution snapshots saved beside USB recordings, with patient-linked image and video albums.
- Added Gmail sending with an encrypted app password, attachment review and a 25 MB combined attachment limit. Sending requires an explicit Send action; uncertain results never retry automatically.
- Added a project Python launcher and VS Code configuration to use the project's installed dependencies.

Validation includes simulated OBS and SMTP workflows, update checksum and download failure tests, and local browser checks. Hardware recording, live Gmail delivery and installation on another Windows PC require operator acceptance testing.
