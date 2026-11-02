# Release notes

## 1.0.59

- Streamed album/recording metadata into bounded newest-page selection instead of retaining every media entry. Kept existing listing and thumbnail improvements.
- Debounced album searches and prevented overlapping status polls while preserving fresh recording state after start/save operations.
- Reduced image-name database reads to patient ID and name, without decoding full scan notes. Added large-album ordering/limit, thumbnail preservation and metadata projection checks.

## 1.0.58

- Updated the branded client quick-start PDF and email template for shortcuts, combined sharing, progress and setup. Releases include the matching PDF when available.

- Added F9 to toggle Start recording and Stop & save on the focused Preview & record page. F8 remains snapshot. F9 retries a pending save instead of starting another recording.
- Blocked shortcuts and duplicate button actions while starting/stopping/saving, including during status refresh. Typing, dialogs, other pages, held keys and recovery blocks do not trigger recording.

## 1.0.57

- Retained the 30-second Gmail connection timeout but allowed up to ten minutes per send operation for slower attachment uploads and confirmation.
- Added specific timeout/connection-loss messages and safe error categories. Uncertain sends still require checking Gmail Sent; no automatic retry is introduced.
- Added send-timeout, pre-transfer failure and real SMTP message-encoding tests without network or email delivery.

## 1.0.56

- Added upload progress showing actual bytes transferred, percentage and files verified. Completion is shown only after upload verification.
- Added animated email progress with preparation, Gmail connection and sending stages; completion appears after Gmail accepts the message. No delivery percentage is estimated.

## 1.0.55

- Added F8 as the primary snapshot shortcut on Preview & record, retaining Ctrl+Alt+S. The handler supports keyboard layouts reporting Ctrl+Alt as AltGr and key events without a physical key code.
- Shortcuts remain browser focused and ignore typing fields, open dialogs, held keys and an in-progress capture.

## 1.0.54

- When selected images exceed 25 MB, offer an explicit option to upload the images and email clickable filename links. Supports image-only and combined video/image emails, without ZIP files.
- Smaller image selections stay as attachments. The reviewed upload choice is bound to the selected files; changing it invalidates the previous upload review.

## 1.0.53

- Email selected videos and images together: videos upload to R2 and appear as clickable filenames; images attach to the same reviewed Gmail email. Selections containing video default to this option.
- The 25 MB attachment limit applies only to images in this mode and is checked before uploading. Images are never uploaded to cloud storage; attachment identity checks and explicit Send remain.

## 1.0.52

- Video emails and the review dialog show each video filename as a clickable link instead of displaying the full signed URL. HTML email safely escapes message text and filenames; a plain-text fallback retains usable URLs.
- Filenames and destinations are frozen with the reviewed email. Existing attachment emails are unchanged.

## 1.0.51

- Cloud sharing uploads original MP4 videos directly, with an individual seven-day email link per video. Removed ZIP packaging and local upload staging.
- Cloud upload accepts videos only; images retain the Gmail attachment option. Failed batches attempt cleanup of their new cloud objects while preserving USB originals.

## 1.0.50

- Replaced manual SwissTransfer sharing with confirmed Cloudflare R2 uploads from saved albums. Selected files are packaged into a neutral ZIP; the reviewed Gmail email contains a seven-day private-object download link. Existing attachment and family USB options remain.
- Added encrypted cloud credentials and a connection check in Settings. Uploads require an eight-day lifecycle deletion rule and keep USB originals. Upload failures never send email or expose raw service errors.
- Added upload workflow, file-change, staging cleanup, retention, credentials and SDK link tests; packaged builds include the S3 client.

## 1.0.49

- Added a SwissTransfer download-link option to album emails, alongside existing attachments and family USB handover. Larger selections open in link mode; attachment emails retain the 25 MB limit.
- Uploads stay manual in the browser. The app can open the selected files' folders, then emails the pasted HTTPS SwissTransfer link using the existing Gmail app-password setup. No OAuth client, browser credential access or automatic third-party upload is introduced.
- Required confirmation that the upload is complete and the link belongs to the selected patient, with a frozen email review before Send. The dialog shows the selected patient's name/ID, clears the recipient and custom message when switching patients, and clears the link for each new selection. Link emails contain no file attachments and never fetch the pasted URL.

## 1.0.48

- Family USB folders now use the patient name, patient ID and copy date/time, for example `Jane Smith - MOL-12345678 - 2026-11-01_143025`.
- Folder labels use saved patient details, with a Windows-safe fallback when the name is missing. Same-second copies add a numbered suffix rather than overwriting existing or unfinished folders.

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
