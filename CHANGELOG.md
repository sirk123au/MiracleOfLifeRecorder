# Release notes

## 1.0.47

- Restored family USB handover in Saved album alongside email, for individual files or a selected group of videos and snapshots from one patient.
- Added background copying with progress and SHA-256 verification. Family USB copies are not subject to email's 25 MB limit; capacity and FAT32 file-size limits are checked before copying.
- Preserved originals and existing family-drive files. Each transfer creates a unique folder, stays marked `.incomplete` until verified, and checks drive identities throughout. Repeated clicks reuse the same transfer status.

## 1.0.46

- Added automatic stable-release checks after startup and every six hours, with verified downloads starting while idle. The Settings option is enabled by default and can be turned off.
- Installed Windows copies apply a ready update silently after Exit Application from the tray. A detached helper waits for process exit, defers if the app is reopened, and rechecks the checksum; it never force-closes programs or reboots Windows. Source and portable runs download only.
- Changed the installer to install the complete application runtime instead of a self-extracting EXE. Launching and closing the installed app no longer creates or removes a PyInstaller `_MEI` temporary folder.
- Kept the same Setup EXE, shortcuts, update process and preserved AppData/USB storage. Standalone builds must keep their EXE and `_internal` folder together.
- Added bounded retries for brief Windows locks on recording-recovery metadata. If cleanup still fails after a successful save, the app identifies cleanup separately and keeps Retry save available without recording or moving the video again.

## 1.0.45

- Added Ctrl+Alt+S to take a snapshot on Preview & record while the browser has focus.
- Displayed the shortcut on the snapshot button. Held-key repeats and overlapping snapshot requests are ignored; patient fields, dialogs and other pages do not trigger it.

## 1.0.44

- New patient video and snapshot folders include the patient ID and name, for example `MOL-12345678 - Jane Smith`.
- Patient names are made safe for Windows folders and long labels are shortened while retaining the complete ID.
- Existing folders and media are preserved. Albums continue to group old and new folders by patient ID.
- Interrupted recordings recover to their original folder, using that scan's saved name. Older ID-only recovery sessions keep their original destination.

## 1.0.43

- Added manual update checking and verified installer downloads from GitHub Releases in Settings.
- Kept updates separate from installation: finish work and exit from the tray before running the installer. Settings, credentials, patient history and USB media are preserved.
- Patient albums now start collapsed and can be expanded individually.
- Added full-resolution snapshots saved beside USB recordings, with patient-linked image and video albums.
- Added Gmail sending with an encrypted app password, attachment review and a 25 MB combined attachment limit. Sending requires an explicit Send action; uncertain results never retry automatically.
- Added a project Python launcher and VS Code configuration to use the project's installed dependencies.

Validation includes simulated OBS and SMTP workflows, update checksum and download failure tests, and local browser checks. Hardware recording, live Gmail delivery and installation on another Windows PC require operator acceptance testing.
