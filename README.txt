MIRACLE OF LIFE ULTRASOUND RECORDER

Windows build instructions:
1. Install Python 3.11 or 3.12 (check Add Python to PATH).
2. Run build_exe.bat.
3. Run dist\MiracleOfLifeRecorder\MiracleOfLifeRecorder.exe.
4. OBS must be running, with WebSocket enabled on port 5555.
5. Select your recording USB drive in Settings, then Save settings.
6. Test with dummy patient information before real use.

Internet testing with your own domain:
Close the existing app first. In PowerShell, run:
  .\start_remote_test.ps1 -BindAddress 192.168.0.235 -Domain test.yourdomain.com
Replace the IP/domain with your actual values. Enter a test password of at
least 12 characters. Browser username: tester. Password is not saved to disk.
The app binds to that LAN IP on port 8765, accepts the configured domain and
requires authentication even for its dashboard. Default app launches remain
localhost-only; the script's network settings apply only to its process.
Your domain's HTTPS reverse proxy must forward to http://192.168.0.235:8765,
preserve the configured Host and Authorization headers, and allow video
downloads/long-running USB copy requests. Configure the Windows firewall to
allow the proxy to reach port 8765. DNS alone does not create a connection.
Keep port 8765 off the public internet; expose the HTTPS proxy instead.
Use dummy patient data and a dedicated test USB. This mode controls real OBS
and USB operations on the host PC. Stop the app after testing. It does not
configure DNS, certificates, router forwarding, firewall or reverse proxy.

Generated EXE and installer builds are written to dist (not tracked in Git).
Original logo supplied by user is embedded in the web header.
Closing the browser leaves the tray application running.
Recording format in OBS must be Hybrid MP4.
Patient medical data requires encrypted storage and access controls.

Recording source selection:
Open Settings > Recording sources and Refresh OBS sources with OBS connected.
The video dropdown groups Video capture devices separately from Existing
OBS sources. Devices come from OBS's Windows DirectShow device list, so USB
capture cards, webcams, phone cameras and OBS Virtual Camera can appear even
when not already added as a scene source. Select the device and audio source,
then Save settings. Device identity is stored in APPDATA. The app reuses a
matching configured OBS input, or creates its own capture input for that
device. It does not change your existing Video Capture Device input. For
custom resolution/FPS, configure the device in an OBS source first. Refresh
the list after connecting/disconnecting devices; unavailable choices are
retained and marked, never silently replaced. The video
source defaults to blackmagic for existing installations, but another OBS
video input may be selected. Only that input is added to the live recording
picture, along with optional app logos. Other video inputs are not copied.
Choose an OBS audio-only input, Use selected video source audio, or Automatic
Spotify / system output. An explicit choice overrides Automatic audio
preference and mutes competing microphone/device/background inputs for the
recording. Previous mixer settings are restored afterward. A missing explicit
source blocks recording; it is not silently replaced. App-managed sources
are hidden from the dropdowns. The original OBS scene selector controls
return-to-preview and microphone routing when automatic audio is used.
Existing source selections are retained if OBS is offline or a list refresh
fails. An unavailable saved source is marked until reselected. In Test mode,
a missing default blackmagic input can fall back to the original scene;
other selected video inputs are used normally. Studio Mode must be off.

Windows installer and updates:
- From version 1.0.46, the installer includes the full runtime beside the app.
  The installed app no longer unpacks itself into a temporary _MEI folder on
  each launch, removing that source of temporary-folder deletion errors on exit.
  Operators still download one Setup EXE and use the same Windows shortcuts.
  When running a developer build directly, keep the entire
  dist\MiracleOfLifeRecorder folder together, including _internal. Copying
  only its small EXE will not work. Build verification checks the runtime
  before an installer is produced. Older dist root EXEs are not used.
- This update does not delete leftover _MEI folders created by other programs
  or older builds. Do not delete temporary folders belonging to running apps.
- The generated setup package is in dist\installer. It installs for the
  current Windows user, with Start menu and optional desktop shortcuts.
  Python is not required on the operator's PC. OBS is installed separately.
- To build the EXE, run build_exe.bat. To build the installer, run
  .\build_installer.ps1 with Inno Setup 6/7 installed, or supply -Compiler
  with the full ISCC.exe path. Local build tools may be placed in .tools.
- Edit version.py for each release. The EXE file metadata and installer use
  that version. The installer uses a permanent AppId and previous install
  location, so running a newer installer updates the same application.
- Finish recording/recovery/copies and exit using the tray menu before
  updating or uninstalling. The installer checks the recorder's named mutex
  and refuses to continue while it runs; it never force-closes recordings.
  Older builds without this mutex must also be closed manually.
- Only one app instance runs per Windows login session, including copies
  started from other folders. A second launch opens the existing dashboard.
- Settings remain in %APPDATA%\OBS-Ultrasound-Web\settings.json. Logos and
  recovery metadata remain in that APPDATA folder. The installer does not
  overwrite or uninstall them. Patient databases stay in APPDATA; videos and snapshots stay on USB.
- Back up the whole APPDATA recorder folder and video USB before updating. A newer app adds
  archive metadata without replacing existing patient history. Older builds
  do not understand archived patients; avoid downgrading a working archive.
- Automatic updates are configurable; manual installation remains available. Install a reviewed setup
  package to update. Builds are not code-signed; validate on a test PC before
  replacing a working version. Installer compilation is not an install or
  upgrade acceptance test.
Installer built with Inno Setup: https://jrsoftware.org/

Automatic app updates (version 1.0.46):
- Automatic updates are enabled by default in Settings > Application updates.
  The app checks the latest published stable GitHub release about 15 seconds
  after launch, then roughly every six hours. New checks and downloads start
  only when no recording, pending save, recovery or email/USB sharing is active.
  Offline errors and rate limits do not stop the app or trigger rapid retries.
- After a newer installer downloads and passes its size/SHA-256 checks, it
  waits for Exit Application from the tray. The app never closes itself in
  the middle of work. Closing only the browser leaves the tray app running.
- On exit, an installed Windows copy starts a hidden helper. It waits for the
  recorder process to terminate, checks that another recorder has not opened,
  re-verifies the installer hash and runs the per-user Setup silently. It
  does not kill processes or reboot Windows, and does not reopen the app
  after an intentional exit. The next normal launch uses the new version.
- If the app is reopened too quickly, Windows blocks installation, or the
  helper cannot finish, the update is postponed. The next Settings visit
  shows a generic result. Open download folder remains available for a
  manual install after closing the recorder. Settings, Gmail credentials,
  patient history and USB media remain in their original locations.
- Untick Automatically check, download and install updates when I exit, then
  Save settings, to disable background checks/downloads and install-on-exit.
  Manual Check for updates and Download update buttons remain available.
- Source runs (app.py) and portable/uninstalled folders check and download
  updates but do not install them automatically. Auto-install is limited to
  a frozen Windows app beside its Inno Setup uninstaller. Install version
  1.0.46 once using the current updater/Setup to enable future automatic
  installations. The old 1.0.45 controller cannot silently update itself.
- Updates come only from
  https://github.com/sirk123au/MiracleOfLifeRecorder/releases
  No GitHub token or login is needed. Patient data, media and email credentials
  are never uploaded. Installers download to APPDATA\OBS-Ultrasound-Web\updates.
- Download sizes and SHA-256 digests must match GitHub's metadata. Missing
  hashes, altered files, unsupported redirects and partial downloads are
  rejected. Checks compare numeric stable versions and never downgrade.
  Downloads are integrity checked but are not code-signed.
- Small helper scripts and install-result.json remain inside the updates
  folder. They contain update paths, checksums and generic status only, with
  no patient data or email credentials. Existing recovery data is never
  removed to bypass an unfinished scan. Automatic installation avoids
  inheriting bundled DLL search paths that could lock runtime files.
- There is no forced Windows reboot or automatic rollback of application
  binaries. If setup fails, check the status and use the downloaded installer
  manually. Backups of patient AppData and USB media remain recommended.

Publishing a new release (developer):
1. Change version.py, run tests, and build with build_installer.ps1.
2. Review the source and CHANGELOG.md. Commit and push to origin/master.
   Never stage APPDATA, recordings, patient images or credentials.
3. Create and push tag v<version> pointing to that exact source commit.
4. Run .venv\Scripts\python.exe packaging\publish_release.py.
   It uses the existing GitHub Git sign-in without saving or printing tokens,
   creates a draft release, uploads the versioned Setup EXE and SHA256SUMS.txt,
   verifies GitHub's asset digests, then publishes it as the latest release.
   It requires a clean source tree, matching local tag and pushed commit.
   Existing published releases/assets are never overwritten. If interrupted,
   inspect the draft on GitHub; rerunning can resume matching asset uploads.
5. Test Settings > Check for updates from the previous version. Installers
   belong in GitHub Release assets; dist remains excluded from source Git.

Opening and closing video logos:
1. Open Settings > Video logos. Upload an opening/closing PNG/JPEG image
   or MP4/MOV/WebM/MKV video (up to 250 MB and 60 seconds). Upload the
   full-frame white corner-logo image separately. H.264 MP4 is recommended
   for video compatibility. Files shown in chat are not automatically installed.
2. Enter the name of your existing ultrasound scene in OBS. Turn OBS Studio
   Mode off. OBS must run on this PC; logo files are stored in APPDATA.
3. Enable the opening/closing logo sequence and Save settings.
   For a still-image ending, upload Fixed outro image (PNG/JPEG). Uploading
   it selects Use a fixed outro image; Save settings. The intro can remain a
   video. Stop & save fades to this separate still, holds it for 3 seconds,
   then saves without trying to restart or play the intro video again.
   Select Replay the intro media to return to the previous ending behavior.
4. Images show for 3 seconds at each end. A video is restarted from its
   first frame and played in full before the 1-second fade to ultrasound.
   Stop & save fades back to the same media and replays the complete video
   before stopping. The final frame is held while fading. Use short clips;
   longer videos extend the start/stop wait. Wait for completion before
   disconnecting the USB.
   When logo branding is enabled, the selected background audio (Spotify,
   system output or explicit audio source) stays muted during that clip.
   After the intro finishes and the fade to ultrasound ends, the corner logo
   and background audio fade in together over one second.
   The intro video's own audio is retained. Spotify itself keeps playing;
   only its capture into the recording is delayed. This fade also applies
   to image intros.
The app creates scenes/sources prefixed MOL Recorder with its own identifier.
It uses the dedicated Blackmagic capture scene for normal recordings and
retains existing audio capture. Do not manually edit app-managed scenes. A white background
fills space around the opening image. OBS applies a black color key to an
opaque black overlay background; original image files are not altered.
The prior scene/transition is restored after a normal stop. A logo error
does not delete the recording; closing-logo errors still attempt Stop & save.
This sequence requires recording and stopping through the app. An OBS crash
or manual stop cannot add a closing logo after the video has already ended.
Test a dummy recording to check framing, transparency, audio and timing
before enabling this on a real scan. Intro-video audio follows OBS media
source routing and may mix with Spotify; verify both in that test. Disable the checkbox to use the prior
recording workflow. No logo assets are embedded in the EXE.
OBS scene/transition API reference:
https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md

Development validation:
Run: python -m unittest discover -s tests -v
The tests simulate OBS and temporary recording folders; they do not contact
OBS or use patient recordings. The EXE build runs these tests first.

OBS connection checks:
On application launch, a background check starts local OBS minimized to the
system tray if it is not already running. The dashboard and tray can open
while this check runs. It reuses the Start OBS button's installation lookup
and duplicate-instance checks. Remote OBS hosts are not launched locally.
Use Start OBS to launch the local Windows installation minimized without
taking focus from the dashboard. An already running OBS window is left as is.
The app checks
registered executable paths and standard Program Files installations, uses
the OBS executable folder as its working directory, and avoids launching a
second instance when OBS is running or still starting. Custom/portable OBS
installations that are not registered must be opened manually. This button
does not change OBS settings; enable its WebSocket server in OBS beforehand.
The dashboard checks the local OBS process, configured TCP port, and an
authenticated OBS WebSocket request on opening and every five seconds while
visible. Use Check connection to retry immediately. Recording also requires
a successful OBS request before starting. No recording is started by a check.
If OBS is closed, open OBS Studio. If the port is unreachable, enable the
server in Tools > WebSocket Server Settings and match the app's host and
port (default 5555). If the port is open but connection fails, check the OBS
WebSocket password and server compatibility. Save settings before checking.
Process detection is for local Windows OBS only; remote hosts report process
status as unavailable and still receive the WebSocket connection check.

Spotify background music:
In Settings > Recording sources, choose an explicit audio source or leave
it on Automatic Spotify / system output. For Automatic audio preference,
choose Spotify directly (with system-output fallback) or System output. Normal Blackmagic recordings create/reuse the
appropriate OBS audio source. The selected recording audio uses Monitor Off
(output to recording), and previous monitoring settings are restored afterward.
Existing Spotify application sources refresh their window target and match by
executable so changing song titles does not break capture.
Spotify uses Application Audio Capture when
available and targets Spotify.exe; if no compatible Spotify source/window
can be selected, the app uses the default Windows speaker output and warns.
System output also records other PC sounds, including notifications. Use the
Windows default playback device that Spotify is actually using.
The active route is shown under Recording details. The selected source is
unmuted and enabled on all six OBS audio tracks. Existing positive mixer
volume is retained; a silent zero level is raised for capture. Duplicate
desktop/application-output captures are temporarily muted when using another
route. Prior mute/volume/track settings are saved in recovery metadata and
restored after stopping, including a recovered stop. Review the mixer if
restoration reports an error. Existing microphone/Blackmagic device audio
keeps its configured behavior. Test mode uses the original scene's picture
with the same selectable background-audio routing, without requiring Blackmagic.
The header shows Spotify playback status, current track and artist, refreshing
approximately every five seconds. Click the status to check again. Use the
adjacent previous, Play/Pause and next icon buttons to control Spotify on the recorder
PC. Open Spotify and select music first if the button is disabled. The app
reads Windows media sessions without a Spotify login. Track details stay in
memory for display; they are not saved in patient records or logs.
The app opens the installed Spotify desktop app minimized at startup if it is
not running. After launch it waits up to 20 seconds for a Spotify-owned window
and minimizes any restored Spotify windows throughout that startup period
without taking focus. After 20 seconds normal manual reopening is allowed.
Already running Spotify
windows are left as they are.
Pressing Record also opens Spotify if needed, briefly waits for its media
session, and keeps playback paused until the intro finishes. Spotify must be installed,
signed in and have music selected.
Open Spotify and select music first. Failed playback requests warn without
blocking recording. Background capture still waits for the intro and fades in.
During a scan, paused/stopped/missing Spotify or an
unavailable playback check shows a warning. Recording is still allowed and
will not be stopped automatically. Resume music using the header Play button
or Spotify itself.
This needs Windows 10 version 1809 or newer with Windows media sessions.
Browser Spotify and Spotify Connect playback on another device are not
verified. Playing status does not prove OBS captures the music, or that its
output is audible/unmuted. Play back a dummy recording to verify music and
volume with the selected audio route before a real scan.
Windows playback API reference:
https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssession

Recording safety and recovery:
- Every recording uses a unique subfolder inside USB _staging.
- Recovery-marker cleanup briefly retries Windows sharing locks. If it still
  fails after the recording is filed and linked, the message distinguishes
  saved media from incomplete cleanup. Retry save clears that same marker;
  it never records or moves the saved video a second time. Keep the USB
  connected and do not manually remove recovery metadata to bypass the check.
- Stop failures keep controls locked so the operator can retry Stop & save.
- Filing failures retain the original and offer Retry save without stopping
  OBS a second time. Restore the same USB drive and retry.
- Never delete _staging or disconnect the drive while recording/saving.
- New recordings write a recovery journal before OBS starts. After a restart,
  reconnect the original USB and OBS. The app restores the interrupted scan
  and offers Stop & save or Retry save. It does not stop OBS automatically.
- Recovery metadata, including the patient ID and volume ID, stays in the
  APPDATA pending-session.json file. USB staging contains only video files.
  Never delete recovery metadata to bypass an unfinished scan.
- Recovery waits if the drive/OBS is unavailable, files are ambiguous or OBS
  is recording another session. New scans remain blocked. If OBS never wrote
  a file, Clear empty interrupted session is available after confirmation;
  it checks OBS is stopped and refuses to clear sessions containing files.
- A background check runs about every three seconds during recording. It
  shows warnings for disconnected USB, low free space, lost OBS connection
  and unexpected recording stops. Warnings do not automatically stop OBS.
  A uniquely identified stopped recording becomes a retryable pending save.
- Recovery tracks scans started by this version. Older staging files and
  damaged/corrupt journals require manual review. Recovery cannot repair a
  corrupt MP4 or guarantee survival of a USB failure or sudden power loss.
- Unsupported output formats are retained for manual recovery. Configure
  Hybrid MP4 in OBS before the next scan.
- Existing destination recordings are never replaced by the Windows rename.
- Select the recording USB in Settings (also required once after upgrading
  from label-based detection). Its volume ID is remembered across drive-letter
  and label changes. A missing or ambiguous USB blocks recording; the app
  never silently switches to another drive.

Privacy:
Patient searches use POST bodies; playback URLs contain temporary opaque IDs.
Patient names, baby birth dates, scan types and notes are stored locally in
%APPDATA%\OBS-Ultrasound-Web\patients\patient-records.sqlite3. Patient IDs also appear in recording folders
and filenames. This database is not encrypted by the application.
Local responses disable caching. The server binds only to 127.0.0.1.
Settings (including the OBS password) remain in the current user's APPDATA
OBS-Ultrasound-Web folder; they are not encrypted by this application.
Protect the Windows account, local APPDATA storage and USB with appropriate
access controls and disk encryption. The app does not encrypt these files.

Storage migration (version 1.0.4 and later):
Close older recorder copies, connect the existing recording USB, then launch
this version. It creates verified local SQLite backups under APPDATA's
migration-backups folder, imports patient history, archives and generated
IDs, verifies the import and only then removes the legacy USB database.
Original videos are not moved. Recovery journals and leftover export
packages also move into APPDATA. If the source changes, is locked, is damaged
or has a conflicting patient ID, the source is retained and migration needs
review. No patient records are silently overwritten. Migrate each old
recording USB on this PC before retiring it. Backups contain patient data.
Video links include the USB volume serial, so changing drive letters is
supported and identical filenames on different USBs are kept separate.
Use the same Windows account on this PC; patient databases no longer travel
with the video USB. Copy/restore APPDATA separately when moving to another PC.

Patient records:
- Generate ID creates an anonymous ID such as MOL-7A2F9C31 for a new family.
  New patient clears the form and generates one automatically. Patient
  management works with the USB disconnected. IDs are reserved locally and
  checked against active/archived patients and any connected patient folders.
  IDs are not reused by the generator.
  This does not create an empty patient; save details or start recording to
  create the actual record. Load returning families instead of generating
  another ID for them. Test IDs have their own namespace.
- If an ID already exists, saving/recording requires loading the existing
  patient or confirming their displayed name and ID. Earlier versions are
  retained; stale confirmations are rejected if details changed meanwhile.
- To retire a patient, select Archive patient beside a search result. This
  hides them from the default search but keeps all history, recording links
  and videos. Archived IDs cannot be used for new details or recordings.
- Check Include archived patients to find and restore an archived patient.
  To permanently delete, archive first, then choose Permanently delete,
  confirm the warning and type the patient ID. This removes stored details
  and history, but keeps recordings and snapshots. Their linked information is then no
  longer available for export. Real/test patient archives are separate.
- Deletion is blocked during recording, pending saves and family USB copies.
  If details changed since the search, search again before deleting.
- Recordings still retain patient IDs in folders/filenames. Existing exports,
  family USB copies and backups are unaffected; this is not a secure erase
  of all patient information across devices or backups.
- Select Save patient details to save without starting a recording.
- Starting a recording also saves a snapshot of the details for that scan.
  Recording will not start if those details cannot be saved.
- Use Find a patient to search by name or ID, load their latest details and
  view their latest 200 history entries. All older entries remain stored.
- Saving changes appends a version; it does not erase earlier entries.
- Use New patient to clear the form before entering a different patient.
- Test patient details use APPDATA\OBS-Ultrasound-Web\test-patients.
- Back up the entire APPDATA\OBS-Ultrasound-Web folder and the USB Ultrasound
  Recordings folder while the app is closed. The USB holds videos and snapshot images only;
  patient records, archives, reserved IDs, settings, logos, recovery metadata
  and export temporary files are in APPDATA. Patient details remain available
  without the USB, but viewing/copying videos requires their original drive.
- Existing recordings remain browsable; details that were never stored by
  older versions cannot be recovered automatically.
- If the application closes between moving a recording and updating its
  database link, the recording remains in its patient folder; review and
  reconcile it manually. USB removal during a write can still damage data.

Patient recording folders (version 1.0.44):
- New real-patient media saves into Patients\<patient ID> - <patient name>,
  for example MOL-12345678 - Jane Smith. Videos and snapshots for the same
  saved patient name share this folder. Test Recordings remains separate.
- Windows-invalid characters are replaced with spaces and long labels are
  shortened to keep paths practical. The full patient ID is always retained.
  A missing/unusable name falls back to the ID-only folder.
- Existing media and folders are never renamed or moved by this change.
  If the patient's name changes later, new saves use the updated folder label;
  earlier media stays in its original folder. The album groups both by ID.
- Recording recovery uses the scan's original saved name, not later patient
  edits. Interrupted sessions from earlier versions keep their ID-only folder.
- Names now appear in USB folder labels. Deleting a patient record keeps the
  media and its folder names; it does not remove names from existing USB files.

Snapshots and saved album (version 1.0.40):
- On Preview & record, press Ctrl+Alt+S to take a snapshot, or click Take
  snapshot. The browser must have focus; this is not a Windows-wide shortcut.
  It does not run on other pages, while editing fields, or inside a dialog.
  Holding the keys does not take repeated images. Wait for the saved message
  before taking another snapshot. Patient and USB checks remain the same.
- Select the patient and choose Take snapshot beside the OBS preview. This
  saves a full canvas-resolution PNG of OBS's current program picture, before
  or during recording. OBS must be connected. During recording, the image
  always belongs to that recording's patient, even if the form was edited.
- Images are saved next to the patient's MP4 videos on the selected USB.
  Test images stay in Test Recordings. Unique filenames never overwrite an
  existing image. Patient details and image links stay in APPDATA.
- Saved album groups images and videos by patient name and ID. Search by
  name/ID, view image thumbnails, open original images or play videos.
  The newest 500 files are shown; search to narrow a large collection.
- Delete requires confirmation and removes only the selected image/video.
  Patient history and other files remain. Files changed since the album
  loaded are rejected. Deletion is blocked while recording/saving/sharing.
  Permanent deletion cannot be undone; existing backups are unaffected.

Family USB handover from the album (version 1.0.47):
- Connect a separate family USB drive. In Saved album, expand the correct
  patient and tick the videos/images to give to the family, then select
  Copy selected to family USB. USB copy on an individual file selects it alone.
- Refresh USB drives, select the correct family drive letter/label, check
  the displayed files and select Copy selected files. The recording drive
  is excluded. USB thumb drives and USB-connected HDDs/SSDs are supported.
- This copies selected MP4 videos and PNG/JPEG images only, with neutral
  filenames such as Ultrasound 01.mp4. Patient records and notes are not
  included. Email sharing remains available separately. USB copying does
  not have the 25 MB email limit; destination free space is checked first.
- Each transfer creates a folder named Patient Name - Patient ID - date/time,
  for example Jane Smith - MOL-12345678 - 2026-11-01_143025. This is the copy
  time on the recorder PC. Missing names use Patient; unsafe characters are
  cleaned and long names shortened while retaining the full ID. Copies in
  the same second add _2, _3, etc. rather than overwrite earlier folders.
  Existing USB files and recording originals are kept. The folder stays marked
  .incomplete until all selected files pass SHA-256 verification. Keep both
  drives connected until the app confirms copying and verification finished.
- Never give an .incomplete folder to the family. A disconnected/replaced
  drive, changed source or verification failure stops the transfer. Failed
  partial folders remain clearly marked; no originals are deleted. Safely
  eject the family USB in Windows after a successful copy.
- The progress display continues in the background if the dialog closes.
  Duplicate clicks reuse the same transfer. A browser reload can recover the
  current tab's status. If status is lost after a crash/restart, check the USB
  before starting another copy; transfers never restart automatically.
- Recording, deletion, settings changes, app exit and automatic installation
  are blocked during the copy. FAT32 cannot hold a single file above 4 GB;
  use a suitable exFAT/NTFS USB for larger videos. The app never reformats it.

Gmail sending with an app password (version 1.0.42):
One-time setup:
1. Enable Google 2-Step Verification for the sending Gmail account.
2. Visit https://myaccount.google.com/apppasswords and create an app password
   for Miracle of Life Recorder. No Google Cloud project/custom app is needed.
   Some work/school accounts, security-key-only accounts and Advanced Protection
   do not offer app passwords. Ask the account administrator if unavailable.
3. Open Settings > Gmail email. Enter the Gmail address and 16-character app
   password, then Save Gmail settings. Spaces in Google's displayed password
   are accepted. Use the app password, never the normal Google password.
4. Select Check connection. This verifies secure SMTP sign-in without sending
   a test email. A saved setting alone does not verify that Google accepts it.
- Credentials are Windows current-user DPAPI encrypted in
  APPDATA\OBS-Ultrasound-Web\gmail\smtp.dpapi. The password is never returned
  to the browser, saved to settings.json or logged. Leave the password blank
  when saving the same address to retain it. Changing the address requires
  its app password. The password field clears after saving, including errors.
- Changing the Google account password revokes its app passwords. Create and
  save a new app password if sign-in stops working. Forget account removes
  local saved credentials; revoke its app password in Google to revoke access.
- Windows user encryption does not transfer to a different PC/user account;
  configure Gmail again there. Old OAuth files from 1.0.40 are left untouched
  but are not used. The installer does not overwrite stored Gmail settings.

Daily use:
- In Saved album, select images/videos from one patient and choose Email
  selected, or Email beside one file. Enter the recipient, subject and message.
  Patient information fields are optional and unchecked by default. Selected
  fields come from the saved scan; differing scan details require separate
  emails or leaving those fields unchecked. Images/videos may themselves
  contain patient information: inspect them before sending.
- Click Review email. Check the sender, recipient, subject, complete message
  and attached filenames/sizes. Click Send email to send exactly that review.
  Editing the form invalidates the review. Nothing sends during preparation,
  connection checks or recording. Sending happens only after Send email.
- Attachments upload automatically; no downloads/copy/paste needed. Neutral
  attachment names (Ultrasound 01.png/mp4) omit patient IDs. Originals on USB
  stay unchanged. Combined attachment size must be at most 25,000,000 bytes;
  larger videos remain on USB for the usual family handover. No Drive upload
  or automatic compression is performed. Encoded emails are capped at 35 MB.
- Keep the original USB connected until sending finishes. Sending temporarily
  blocks new recording, deletion, copies and settings changes. Attachment bytes
  and their drive identity are checked against the reviewed files.
- The app uses smtp.gmail.com on port 465 with certificate-verified TLS.
  Gmail accepting an email does not guarantee recipient delivery. Review Gmail
  Sent and any bounce messages when needed. Google account sending limits apply.
- Duplicate clicks reuse the same send status and never send twice. Nothing
  retries automatically. A timeout or lost confirmation may mean Gmail already
  accepted the email: check Gmail Sent before making a new review and resending.
- A small status journal in APPDATA\OBS-Ultrasound-Web\gmail\send-status.json
  contains opaque job IDs and generic results only, with no patient details,
  recipient addresses, message contents or credentials. Interrupted sends show
  an unknown result after restart; no background resend occurs. Browser reloads
  recover the current tab's status through an opaque session-storage job ID.
- Status history keeps the newest 200 jobs. Email reviews expire after 15
  minutes. No email addresses, patient details or message bodies enter URLs.
- No Google credentials are bundled. Live sending must be checked with your
  account and dummy data after configuration; automated tests simulate SMTP.
Google setup help: https://support.google.com/accounts/answer/185833

Before replacing a working EXE:
Build and test a separate candidate using dummy patient information. Verify
ATEM audio/video, the faster JPEG snapshot preview, Hybrid MP4 playback,
USB removal/reconnection recovery, tray actions, and recording with low free
space. Hardware capture, power-loss recovery and a packaged EXE have not
been validated by the automated tests. Keep the previous working EXE.

PC performance presets:
Settings > PC performance offers Low performance PC (540p preview, up to 2 fps)
and High performance PC (1080p preview, up to 15 fps). Save settings to apply;
the selection is retained across restarts. High is the existing default.
These presets affect preview load only, not recording resolution, frame rate
or audio. Preview remains OBS snapshots, with actual speed dependent on OBS
and PC performance. Preview pauses on hidden pages. Try a dummy recording
and select Low if the preview affects recording performance.

Recording health and pre-session test:
In Settings > Pre-session test, Record a 10-second test uses generated TEST details and
the separate test database/folder. Current patient form values are ignored
by the test request and remain in the form. Use a test picture without patient
information. The test plays the configured intro, records ten seconds of live
video, plays the outro and saves through the normal protected save workflow.
A server-side timer stops it even if the browser closes. Stop & save can also
finish early. A failed stop/save remains available for retry or recovery.
After saving, use the test player to check picture, music, intro and outro.
The clip is also available under Saved recordings and is not deleted silently.

The health panel shows OBS frame rate, warns on high OBS CPU use and newly
skipped rendering/encoding frames, and checks the selected recording source's
OBS audio meter. Audio signal detected means OBS has received a recent signal;
it does not prove the saved file contains correct sound. Quiet, muted,
intro-delayed and unavailable checks are shown separately. Inspect the preview
and play back the test to verify picture and sound. Meter samples stay in memory;
no captured audio, source names or health data are written to patient records.

USB readiness panel:
The compact panel below the page content refreshes every five seconds while visible.
It shows the selected drive, remaining and total capacity, and a warning when
the USB is missing, ambiguous, unreadable or at/below the configured free-space
reserve. It never selects another drive automatically.
Recording time is approximate and excludes the free-space reserve. It uses a
recent OBS recording's average data rate when available (after five seconds of
recording); otherwise it explicitly assumes 20 Mbps. It does not change OBS
bitrate. Actual capacity varies with encoding settings and picture content.

External USB recording disks:
Settings > Recording USB drive supports both thumb drives and USB external
HDDs/SSDs. Fixed disks are included only when a read-only Windows storage
query confirms the USB bus type; internal SATA/NVMe disks are not added as USBs.
The same detection is used for USB readiness, recording and family USB copy.
Click Refresh USB drives, select the external disk and Save settings. Existing
volume-ID protection and handling of drive-letter changes are unchanged.
Storage descriptor reference:
https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-storage_device_descriptor

Spotify during recording:
Record opens Spotify if needed for source setup and keeps it paused during
the intro, then restarts the current song
using Spotify's playback-position control when live capture begins after the
intro. The existing audio/overlay fade is retained. No previous-track command
is used to restart the song. After the outro and confirmed OBS stop, Spotify
is paused. An unexpected OBS stop also pauses it once detected. A failed stop
confirmation does not pause music while recording might still be running.
Spotify control failures warn without preventing video recording or saving.

Recording timer:
The large timer starts immediately when Start recording is pressed and counts
through preparation and the intro. Browser ticks do not wait for OBS requests.
The server records the start-request time so a page reload restores elapsed
time. A confirmed failed start or stopped recording resets the timer.

Running from VS Code / source:
Use the Run and Debug configuration "Miracle of Life Recorder (.venv)" (F5),
or double-click start_app.bat. Both explicitly use the project's .venv Python.
In a terminal, run .venv\Scripts\python.exe app.py.
A different Python installation can run Flask/OBS but lack the Windows media
packages required for Spotify, causing the header to show Check needed.
If needed, select .venv\Scripts\python.exe using Python: Select Interpreter,
then open a new terminal. Existing terminals may still use the old interpreter.
Close the existing recorder from its tray menu after finishing any recording
before relaunching; single-instance protection otherwise reopens the old app.
