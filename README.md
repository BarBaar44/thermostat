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

## Thermostatic Radiator Valve (TRV)
- Sonoff TRVZB
- Shelly TRV Blu

Yes, you got that right. I use 2 kinds of TRV's. Here are the pros and cons. which will also explain why I use both

### Sonoff TRZB

### Shelly TRV Blue
