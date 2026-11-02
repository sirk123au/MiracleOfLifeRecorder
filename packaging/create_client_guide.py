"""Generate client instructions from the current version without loading patient data."""
import ast
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from version import __version__ as VERSION

OUT=ROOT/'dist'/'client'
INSTALLER=f'https://github.com/sirk123au/MiracleOfLifeRecorder/releases/download/v{VERSION}/MiracleOfLifeRecorder-Setup-{VERSION}.exe'
RELEASE=f'https://github.com/sirk123au/MiracleOfLifeRecorder/releases/tag/v{VERSION}'
PDF=f'https://github.com/sirk123au/MiracleOfLifeRecorder/releases/download/v{VERSION}/Miracle-of-Life-Quick-Start-{VERSION}.pdf'


def create():
    OUT.mkdir(parents=True,exist_ok=True)
    tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
    original=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in node.targets))
    logo=re.search(r'<header>.*?<img src="(data:image/png;base64,[^"]+)"',original,re.S).group(1)
    styles=(ROOT/'packaging'/'client_guide.css').read_text(encoding='utf-8')
    def sheet(number,title,subtitle,content):
        return f'''<section class="sheet"><div class="brand"><img src="{logo}" alt="Miracle of Life original logo"><span>ULTRASOUND RECORDER<br>Client quick-start guide &middot; v{VERSION}</span></div>
        <p class="eyebrow">{subtitle}</p><h1>{title}</h1>{content}
        <footer><span>Miracle of Life Ultrasound Recorder &middot; Windows</span><span>{number} / 3</span></footer></section>'''

    first=f'''<p class="intro">Install the update, check your equipment, then record from the usual browser dashboard.</p>
    <div class="download"><strong>Windows update &middot; version {VERSION}</strong><a class="button" href="{INSTALLER}">Download the Windows installer</a><p class="small">Alternative: open the <a href="{RELEASE}">release page</a> and select MiracleOfLifeRecorder-Setup-{VERSION}.exe.</p></div>
    <h2>1. Install the update</h2><ol><li>Finish any recording, save, copy or email. Back up the recording USB and patient data before updating.</li>
    <li>Right-click the recorder icon in the Windows system tray and choose <b>Exit Application</b>. Closing the browser alone does not exit the app.</li>
    <li>Run the installer, follow the prompts, then reopen the recorder from its usual shortcut. Existing settings, patient history and USB media are kept.</li>
    <li>Make a short test recording and play it back before the next appointment. The pre-session test is in <b>Settings</b>.</li></ol>
    <h2>2. Prepare for the scan</h2><ul><li>Connect Blackmagic and your selected recording USB. Check the bottom USB status bar for readiness and remaining space.</li>
    <li>Check OBS is connected, the ultrasound is visible, and the selected audio/music is correct.</li>
    <li>Enter or find the patient. Use <b>Generate ID</b> for a new patient and enter the name, optional email address, baby&rsquo;s expected birth date and scan type.</li></ul>
    <h2>3. Record and save</h2><p>Open <b>Preview &amp; record</b> and check the patient name and ID. Use <b>Start recording</b>, then <b>Stop &amp; save</b> at the end. Wait for the saved confirmation before removing the USB.</p>
    <div class="card"><h2>Keyboard shortcuts</h2><p><span class="key">F9</span> Start recording; press again to <b>Stop &amp; save</b>.<br><span class="key">F8</span> Take a snapshot. <b>Ctrl + Alt + S</b> is also supported.</p><p>Use these on <b>Preview &amp; record</b> with the browser focused. Some laptops require <b>Fn + F9</b> or <b>Fn + F8</b>. Shortcuts ignore held keys, typing and open dialogs. F9 waits during start/save and retries a pending save instead of starting another recording.</p></div>
    <div class="note">Spotify stays paused during the intro and starts when the live scan begins. It pauses after recording stops. Check playback of a test clip to confirm the saved sound.</div>'''

    second='''<p class="intro">Videos and snapshots stay on the recording USB. Patient records and settings stay in AppData on the recorder PC.</p>
    <h2>4. Find the saved album</h2><p>Open <b>Saved recordings</b> and search by patient name or ID. Albums start collapsed; click a patient heading to show the files. Review the selected images and videos before sharing.</p>
    <h2>5. Copy to the family USB</h2><ol><li>Connect a separate family USB. Select the correct files in one patient album and choose <b>Copy selected to family USB</b>.</li>
    <li>Refresh the drive list, choose the family USB, then select <b>Copy selected files</b>.</li><li>Wait for copying and verification to finish, then safely eject the family USB in Windows.</li></ol>
    <p class="compact">The output folder includes the patient name, patient ID and copy date/time. Existing files and originals are kept. Never give the family a folder marked <b>.incomplete</b>.</p>
    <h2>6. Email images and videos together</h2><ol><li>Select files in <b>one patient album</b>, then choose <b>Email selected</b>.</li>
    <li>For videos, use <b>Upload links + image attachments</b>. Check the patient and files, confirm uploading and select <b>Upload selected videos</b>. Videos upload directly; no ZIP file is created.</li>
    <li>Images totalling <b>25 MB or less</b> attach to the same email. For larger image selections, choose <b>Images exceed 25 MB: upload images too</b>, then upload the selected files. This also works for images without a video.</li>
    <li>Check the recipient and enter your message. The patient&rsquo;s saved email address is filled in when available and can be edited. Patient-information fields are optional and unchecked by default.</li>
    <li>Select <b>Review email</b>. Check the recipient, clickable filenames and attachment list, then select <b>Send email</b>. Keep the USB connected until image attachments finish sending.</li></ol>
    <div class="note"><b>Download links last seven days after upload.</b> Anyone with a link can open its file. Send only to the intended family and tell them to download their copies before expiry. Originals on the recording USB are kept.</div>
    <div class="columns"><div class="card"><h2>Upload progress</h2><p>Shows transferred size, percentage and files verified. It reaches <b>100%</b> only after the uploaded files are checked.</p></div><div class="card"><h2>Email progress</h2><p>Shows preparation, Gmail connection and sending. Completion means Gmail accepted the message; it does not prove delivery.</p></div></div>
    <p class="small">If sending is uncertain, check Gmail <b>Sent</b> before retrying. The recorder never automatically resends an uncertain email.</p>'''

    third='''<p class="intro">Complete these settings once. Day-to-day recording does not require changing them.</p>
    <h2>Gmail setup</h2><ol><li>Enable Google 2-Step Verification, then create a <a href="https://myaccount.google.com/apppasswords">Google app password</a>. Some managed accounts do not allow this; ask the account administrator if the option is missing.</li>
    <li>In <b>Settings &rarr; Gmail email</b>, enter your Gmail address and the app password. Your normal Google password will not work.</li>
    <li>Select <b>Save Gmail settings</b>, then <b>Check connection</b>. The check signs in without sending an email. Credentials are encrypted for the current Windows user.</li></ol>
    <h2>Cloudflare R2 setup for download links</h2><p class="compact">Ask your installer/support person to complete this section if needed. Family USB copying and small attachment emails remain available without cloud sharing.</p>
    <ol><li>Create a dedicated <b>private R2 bucket</b> using Standard storage. Leave public access disabled. Create S3 credentials with object read/write and bucket lifecycle-read permission.</li>
    <li>Add an enabled lifecycle rule for prefix <b>miracle-of-life/</b> to delete uploaded objects after <b>8 days</b>. The app checks this rule before uploading. Cloud deletion can take longer than the configured period.</li>
    <li>In <b>Settings &rarr; Cloud sharing</b>, enter the Cloudflare Account ID, bucket name, Access Key ID and Secret Access Key. Select <b>Save cloud settings</b> and <b>Check connection</b>.</li></ol>
    <p class="small">Keys are encrypted in AppData. Cloudflare has a limited free allowance; extra usage may be billed. Monitor usage. Forgetting keys does not delete existing uploads. The download link itself grants access until expiry.</p>
    <h2>Automatic updates</h2><p>In the installed app, updates download while idle and install when you choose <b>Exit Application</b> from the tray. They do not interrupt recording or sharing. Manage this under <b>Settings &rarr; Application updates</b>.</p>
    <h2>If something needs attention</h2><dl class="support"><dt>USB missing</dt><dd>Reconnect the selected drive. Keep it connected while recording, saving, copying or sending attachments.</dd>
    <dt>Retry save</dt><dd>Keep the original drive connected. Follow the message; do not delete staging or recovery files.</dd>
    <dt>No picture/audio</dt><dd>Check OBS connection, selected sources and Spotify. Record and play back a short test.</dd>
    <dt>Email uncertain</dt><dd>Check Gmail Sent before retrying. If still failing, note the exact warning without patient details or passwords and contact support.</dd></dl>'''

    document='<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Miracle of Life quick-start guide</title><style>'+styles+'</style></head><body>'+sheet(1,'Ready for your next scan','Update &amp; everyday recording',first)+sheet(2,'Share the memories','Albums, family USB &amp; email',second)+sheet(3,'One-time setup &amp; help','Settings &amp; support',third)+'</body></html>'
    (OUT/f'Miracle-of-Life-Quick-Start-{VERSION}.html').write_text(document,encoding='utf-8')
    email=f'''Subject: Miracle of Life Recorder update - version {VERSION}

Hi [Client name],

The latest Miracle of Life Ultrasound Recorder update is ready:
{INSTALLER}

This update includes:
- F9 to start recording, then stop and save with the same key.
- F8 for snapshots (Ctrl+Alt+S also works).
- A saved patient email address to prefill sharing.
- One email with clickable video links and image attachments.
- An upload-link option when selected images exceed 25 MB.
- Upload progress, email sending status and improved Gmail error handling.
- Family USB copying remains available.

The shortcuts work on Preview & record with the browser focused. Some laptops need Fn+F9 or Fn+F8.

Before installing, finish any recording or transfer and choose Exit Application from the recorder's Windows system-tray icon. Closing the browser alone does not exit the app. Run the installer, then reopen the recorder from its usual shortcut.

Your existing settings, patient history and USB recordings are kept. Please make a short test recording after updating and check the saved picture, music and snapshots before the next appointment.

I've attached the updated quick-start PDF, covering everyday use, sharing and one-time email/cloud setup. You can also download it here:
{PDF}

Cloud sharing needs the one-time Cloudflare R2 setup if it has not already been completed. Download links last seven days, so please remind families to save their files before expiry. If an email result is uncertain, check Gmail Sent before trying again.

If you need any help, please let me know.

Kind regards,
[Your name]
'''
    (OUT/f'Update-email-{VERSION}.txt').write_text(email,encoding='utf-8')
    print(f'Created client guide HTML and email template for {VERSION}.')


if __name__=='__main__':create()
