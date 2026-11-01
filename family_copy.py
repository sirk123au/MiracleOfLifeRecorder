"""Verified family media handover; exclusive new folders, no media deletion."""
import hashlib
import os
import shutil
from datetime import datetime
import patient_folders


class CopyError(Exception):pass


def folder_label(items):
    patient_id=items[0].get('patient_id') or 'UNKNOWN'
    name=next(((item.get('details') or {}).get('name','').strip() for item in items if (item.get('details') or {}).get('name','').strip()),'')
    if not name:
        for item in items:
            stored_id,stored_name=patient_folders.identity(item['path'].parent.name)
            if stored_id.casefold()==patient_id.casefold() and stored_name:
                name=stored_name;break
    safe=patient_folders.folder_name(patient_id,name)
    name=safe[len(patient_id)+3:] if safe.startswith(patient_id+' - ') else 'Patient'
    # A name such as CON.txt is reserved on Windows, even with more text after it.
    if name.split('.')[0].rstrip().upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        name='_'+name
    return name+' - '+patient_id+' - '+datetime.now().strftime('%Y-%m-%d_%H%M%S')


def check_capacity(items,destination):
    if not items:raise CopyError('Select at least one image or video.')
    if destination.get('filesystem','').upper()=='FAT32' and any(item['size']>0xFFFFFFFF for item in items):
        raise CopyError('A selected video is too large for this FAT32 USB. Use a suitable exFAT or NTFS drive; the app will not reformat it.')
    if shutil.disk_usage(destination['root']).free<sum(item['size'] for item in items)+1024*1024:
        raise CopyError('The family USB does not have enough free space. Choose another drive or fewer files.')


def copy_selected(items,destination,validate,progress):
    validate();check_capacity(items,destination)
    base=folder_label(items)
    for number in range(1,10001):
        folder=base+(f'_{number}' if number>1 else '')
        incomplete=destination['root']/(folder+'.incomplete')
        if (destination['root']/folder).exists():continue
        try:incomplete.mkdir(exist_ok=False);break
        except FileExistsError:continue
    else:raise CopyError('Could not create a unique family folder. Existing files were kept.')
    if incomplete.resolve().parent!=destination['root'].resolve():raise CopyError('The family USB folder could not be verified.')
    total=sum(item['size'] for item in items);done=0
    for index,item in enumerate(items,1):
        validate()
        suffix=item['path'].suffix.lower()
        if suffix not in ('.png','.jpg','.jpeg','.mp4'):raise CopyError('Unsupported media type.')
        target=incomplete/(f'Ultrasound {index:02d}'+suffix)
        progress(message=f'Copying file {index} of {len(items)}...',copied_bytes=done,total_bytes=total,completed_files=index-1,total_files=len(items))
        digest=hashlib.sha256()
        with item['path'].open('rb') as source,target.open('xb') as output:
            before=os.fstat(source.fileno())
            identity=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
            if identity!=item['fingerprint']:raise CopyError('A selected file changed. Refresh the album and select it again. Originals are unchanged.')
            remaining=item['size']
            while remaining:
                chunk=source.read(min(1024*1024,remaining))
                if not chunk:raise CopyError('A selected file changed during copying. Originals are unchanged.')
                output.write(chunk);digest.update(chunk);remaining-=len(chunk);done+=len(chunk)
                progress(copied_bytes=done)
            after=os.fstat(source.fileno())
            if source.read(1) or (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=identity:
                raise CopyError('A selected file changed during copying. Originals are unchanged.')
            output.flush();os.fsync(output.fileno())
        validate()
        progress(message=f'Verifying file {index} of {len(items)}...')
        copied=hashlib.sha256()
        with target.open('rb') as verify:
            while chunk:=verify.read(1024*1024):copied.update(chunk)
        if copied.digest()!=digest.digest() or target.stat().st_size!=item['size']:
            raise CopyError('Copy verification failed. Keep the originals and do not use the unfinished USB copy.')
        progress(completed_files=index)
    validate()
    complete=destination['root']/folder
    if complete.exists():raise CopyError('The destination folder already exists. Existing files were kept.')
    incomplete.rename(complete)
    return folder
