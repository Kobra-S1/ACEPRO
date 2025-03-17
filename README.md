#This is fork from 

https://github.com/printers-for-people/ACEResearch.git

AND

https://github.com/utkabobr/DuckACE.git


#What is this

A Work-In-Progress driver for Anycubic Color Engine Pro for SOVOL SV08 or any Klipper based 3D Printer
- What is working:
 * Filament load / unload
 * Filament automatic feed
 * Set cutting position
 * Set load amount of filament

- What is not working (yet)
 * detect filament runout
 * proper error handling
 * continue printing on runout from another spool
 * Klipper screen interface
 * Mainsail interface
 * Install script

<!-- GETTING STARTED -->
## Getting Started

You will need:
	Anycubic ACE Pro
	Sovol SV08 --> no mainline klipper esential
	Filament spliter (I used this: https://wiki.bambulab.com/en/parts-acc/ptfe_adapter)
	Filament cutter on the extruder. (I used this: https://www.printables.com/model/1099177-sovol-sv08-head-filament-cutting-mod)
	Filament purge bucket: (https://www.printables.com/model/1209163-sv08-purge-bucket)
	A USB cable trimmed end


![Pins](/img/connector.png)


Connect them to a regular USB port of the printer.


#INSTALL

Do the HW install this not covered here.

 
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

Modify ace.cfg and set corresponding data.