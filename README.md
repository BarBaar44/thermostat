To remove the cloud dependency for my house heating, I decided to create this in Home Assistant. The requirements I set are:
- No cloud dependency
- Multi-room support
- Fully costimzable to my needs
- Auto on/off when somebody/nobody is home

Nice to have:
- Open Window detection

# Hardware

## Used boiler:
- Atag E325ec - 2015. This boiler supports the OpenThem Protocol

## Thermostat
- DIYLess OpenTherm thermostat with custom ESPHome config

Because I want full control, I immediately pushed ESPHome onto the thermostat. But the DIYLess Thermostat has software on it that should work..

I mostly got my config from XXX and XXX

As I already have an OpenTherm Gateway, I already knew what sensors my boiler supports. I basically trimmed down the sensor config towards the sensor my boiler supports.

The hardest part of getting the thermostat dailed in is setting the PID-controller. ESPHome has an autotune function for this. But this only helped me so much. I ran the autotune once to get some initial values. It wasn't long before I found that with the parameters of the autotune, my boiler never got to the actual temperature. Digging around to find the actual meaning of those parameters, I was about to give up, but then I just asked ChatGPT for some help and it got me in the right direction. Awesome AI for once!

## Thermostatic Radiator Valve (TRV)
- Sonoff TRVZB
- Shelly TRV Blu

Yes, you got that right. I use 2 kinds of TRV's. For two radiators, as I have limited space for the TRV's. The Sonoff TRV's just didn't fit, so I had to fall back to the Shelly's. The Shelly TRV's are very much like the Tado's. Both in dimension and in appearance. Here are the pros and cons.

### Sonoff TRZB
Pros:
- Cheaper
- Completely local
- Supported by Zigbee2MQTT

Cons:
- A little bigger than Tado (and Shelly)
- Control is somewhat complicated. You can control the valve opening, but not very straightforward. At least not when using Zigbee2MQTT

### Shelly TRV Blue
Pros:
- Small, about the same size as Tado TRV's
- Possible custom firmware?

Cons:
- Expensive
- Still dependent on a third party
- you need a stick to control them
- You need another integration in HA (Shelly)
- Very limited control.
