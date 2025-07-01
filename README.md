#This is fork from 

https://github.com/printers-for-people/ACEResearch.git

AND

https://github.com/utkabobr/DuckACE.git


#The driver

A Work-In-Progress driver for Anycubic Color Engine Pro for SOVOL SV08 or any Klipper based 3D Printer

## Pinout

![Pins](/img/connector.png)

Connect them to a regular USB.

You will need a filament sensor on toolhead and one at the end of the splitter!!! 

I use BAMBULAB filament splitter.

For hotend use <a href="https://www.printables.com/model/1133951-v4-toolhead-ideal-for-mmu-for-sv08-and-any-voron-g">Nadir extruder</a>

I used <a href="https://www.printables.com/model/1099177-sovol-sv08-head-filament-cutting-mod">Mr Goodman BAMBULAB hotend with cutter</a> mod.

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


#############################################
Endlesspool feature
Switching sequent from starting slot to the next automatically Use up the loaded filaments during the print.
To enable
ACE_ENABLE_ENDLESS_SPOOL
To disable:
ACE_DISABLE_ENDLESS_SPOOL
(If disabled the runout sensor will pause the print)


The endless spool process now:
Detects runout → Immediate response
Disables feed assist on empty slot
Feeds new filament until extruder sensor triggers
Enables feed assist on new slot
Updates and saves current tool index
Continues printing seamlessly

##############################################
Inventory managment:
ACE_SET_SLOT:  Set slot inventory: INDEX= COLOR= MATERIAL= TEMP= | Set status to empty with EMPTY=1

ACE_QUERY_SLOTS: Query all slot inventory as JSON






