"""Shared application artwork for the tray and Windows executable."""
from PIL import Image, ImageDraw


def tray_icon_image():
    image=Image.new('RGB',(64,64),(117,76,96))
    d=ImageDraw.Draw(image)
    d.rounded_rectangle((3,3,60,60),radius=13,fill=(117,76,96),outline=(238,205,219),width=2)
    d.ellipse((13,11,51,49),outline=(255,248,250),width=3)
    d.text((19,23),'M',fill=(255,255,255))
    return image
