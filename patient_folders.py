"""Stable Windows-safe labels for patient media folders; never renames old media."""
import re
import unicodedata


def folder_name(patient_id,name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',patient_id):raise ValueError('Invalid patient ID')
    text=unicodedata.normalize('NFC',name)
    text=''.join(' ' if char in '<>:"/\\|?*' or unicodedata.category(char).startswith('C') else char for char in text)
    text=' '.join(text.split()).strip(' .')
    # Keep the whole component short, including for long patient IDs and emoji.
    remaining=96-len(patient_id)-3
    label=''
    for char in text:
        if len((label+char).encode('utf-16-le'))//2>remaining:break
        label+=char
    label=label.rstrip(' .')
    return patient_id+' - '+label if label else patient_id


def identity(folder):
    """Fallback identity for media without database links, including legacy folders."""
    patient_id,separator,name=folder.partition(' - ')
    if separator and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',patient_id):return patient_id,name
    return folder,''


def id_exists(root,patient_id):
    patients=root/'Patients'
    if not patients.is_dir():return False
    return any(path.is_dir() and identity(path.name)[0].casefold()==patient_id.casefold() for path in patients.iterdir())
