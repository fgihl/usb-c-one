Product: HIL USB-C control

Scope design a pcb for HIL test, with power and data control of signals
The device should manage the power and data lines to the HIL test device based on the control signals received from the Raspberry Pi.


Input 
(must) USB-C-data : Usb-c connector where we get USB 'data'. This is typically connected to a Raspberry Pi 4 or 5.
(perhaps) USB-C-power : USB-C connector where we get power. This is typically connected to a USB charger or other power source.
(perhaps) 5v plug : A 5V power plug that can be used as an alternative power source for the HIL control device. Unclear is this is possible, low prio.



Output
(must) USB-C : USB-C connector wich we connect to our HIL test device.



Possible solution:
* Use cp2102 USB to UART bridge as 'control IC' .It has GPIO which can be used to control the power and data lines to the HIL test device.

Question
* Do we need a MCU to control PD. Perhaps BC1.2 is sufficient?


Only data is need. I.e.more protocol like display port is not needed.



**Scenarios to support**

Raspberry Pi with power from Raspberry PI
=========================================
We connect the raspberry Pi's to the input USB-C (data) connector of our device.
We connect the HIL device to the output USB-C connector of our device.
Raspberry pi can also instruct the PCB to 'remove the usb-c cable' on the HIL device, effectively cutting off power and data lines.
Raspberry pi provides power to the HIL device.

Raspberry Pi with external power (split cable)
================================
We connect the raspberry Pi's to the input USB-C (data) connector of our device. The cable is a split-cable where the VBus line is connected to an external power source.
We connect the HIL device to the output USB-C connector of our device. 
Raspberry pi can also instruct the PCB to 'remove the usb-c cable' on the HIL device, effectively cutting off power and data lines.
It is the split-cable that provides power to the Raspberry Pi separately from the data lines.

Raspberry Pi usb-charger providing power to the HIL control device.
==============================================
We connect the raspberry Pi's to the input USB-C (data) connector of our device.
We connect the HIL device to the output USB-C connector of our device. 
We connect a USB-C cable from the USB charger to the USB-C (power) input of our device.
Raspberry pi can also instruct the PCB to 'remove the usb-c cable' on the HIL device, effectively cutting off power and data lines.
It is the usb-charger that provides power to the HIL control device.


Raspberry Pi, where 5v power plug is provided the power to the HIL control device.
==============================================
This scenaro is option and can be skipped if it's hard do implement.
We connect the raspberry Pi's to the input USB-C (data) connector of our device.
We connect the HIL device to the output USB-C connector of our device. 
We connect the 5V power adaptor to the 5V input of our device.
Raspberry pi can also instruct the PCB to 'remove the usb-c cable' on the HIL device, effectively cutting off power and data lines.
It is the 5V power plug that provides power to the HIL control device.