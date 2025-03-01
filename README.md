#This is fork from 

https://github.com/printers-for-people/ACEResearch.git

AND

https://github.com/utkabobr/DuckACE.git


#The driver

A Work-In-Progress driver for Anycubic Color Engine Pro for SOVOL SV08 or any Klipper based 3D Printer

## Pinout

![Pins](/img/connector.png)


Connect them to a regular USB.


#INSTALL

##Clone rep
clone to the home driectory of the Klipper usually /home/<user>/
git clone https://github.com/szkrisz/ACEPROSV08.git

place a symlink to the ace.py to ~/klipper/klippy/extras/

ln -sf ~/aceprosv08/extras/ace.py ~/klippy/extras/ace.py


place a symlink to the ace.cfg to ~/printer_data/config/

ln -sf ~/aceprosv08/ace.cfg ~/printer_data/config/ace.py


##Install pyserial 4.5 or higher:

Klipper python env must be activated and the pyserial must be update in the env.

virtualenv -p python3 klippy-env

source klippy-env/bin/activate

pip3 install pyserial --upgrade


##Include ace.cfg in the printer.cfg

[include ace.cfg]
