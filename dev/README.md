# Test environment

A throwaway Home Assistant with a simulated house, to try Cloudless Multiroom Thermostat without touching a real heating system.

Nothing here controls real hardware. The `heating_sim` integration fakes everything:

| What | Entities |
|---|---|
| Room temperature sensors | `sensor.sim_<room>_temperature` |
| TRVs (regulate their own valve) | `climate.sim_<room>_trv_<n>` |
| TRV external temperature inputs | `number.sim_<room>_trv_<n>_external_temperature` |
| Boiler thermostat (ESPHome / DIYLess / OTGW stand in) | `climate.sim_boiler` |
| Boiler relay (on/off heat source) | `switch.sim_boiler_relay` |
| Central heating enable | `switch.sim_ch_enabled` |
| Outdoor temperature | `sensor.sim_outdoor_temperature` |
| Windows and presence (toggle these yourself) | `input_boolean.sim_living_room_garden_door`, `input_boolean.sim_master_bedroom_window`, `input_boolean.sim_somebody_home` |

Rooms: Living Room (3 TRVs, garden door), Office, Sports Room, Master Bedroom (2 TRVs, window) and Attic (no TRVs, only adds demand).

Room temperatures follow a simple model: a room warms up while the boiler fires and its TRVs are open, and cools towards the outdoor temperature, faster with a window open. A TRV opens when its target is above what it measures: the external input when it got one in the last 2 hours, otherwise its own sensor, which reads 1.5 °C warm like a real TRV on a radiator. At `speed: 30` one hour passes in two minutes.

## Start

Two options: Docker on any machine, or a VM in Virtual Machine Manager (libvirt/KVM).

### Option 1: Docker

Requires Docker.

```bash
cd dev
docker compose up -d
```

Open http://localhost:8124 (port 8124 so it never clashes with a real HA on 8123) and finish onboarding.

The integration is mounted read only from `../custom_components/`, so after changing its code: `docker compose restart`.

### Option 2: VM in Virtual Machine Manager (Fedora)

`vm/create-vm.sh` builds a Debian 13 VM on your machine and lets cloud-init do the rest: it installs Docker, clones this repo, starts the same test HA and, unless you pass `--no-setup`, does onboarding and creates the heating system with all rooms. The VM shows up in Virtual Machine Manager like any other.

```bash
sudo dnf install virt-install qemu-img   # once, if not installed yet
cd dev/vm
./create-vm.sh                           # VM "cmt-test", runs the branch you have checked out
```

It asks for sudo (disks live in `/var/lib/libvirt/images`), downloads the Debian cloud image once (~400 MB), and prints the VM's address. First boot takes about 5 to 10 minutes, then:

- Home Assistant: `http://<vm-ip>:8124`, login `test` / `testtest`
- SSH: `ssh test@<vm-ip>` (your `~/.ssh/id_ed25519.pub` or `id_rsa.pub` is added, password works too)
- Follow the first boot: `ssh test@<vm-ip> sudo tail -f /var/log/cloud-init-output.log`
- Pull new code from the branch and restart HA: `ssh test@<vm-ip> sudo cmt-update`
- Remove: `./create-vm.sh --delete`

Options: `--name`, `--branch`, `--memory`, `--vcpus`, `--disk`, `--username`, `--password`, `--ssh-key`, `--no-setup`. Another storage pool: `CMT_IMAGES=/path ./create-vm.sh`. The VM runs the `stable` Home Assistant image, so it tests against the current HA release.

## Add the heating system

On the VM this is already done for you (unless `--no-setup`).

Either through the UI (Settings > Devices & services > Add integration > Cloudless Multiroom Thermostat, then "Add room" per room), or with the script, which runs the same forms through the API:

1. Create a long-lived access token: your profile > Security > Long-lived access tokens.
2. Run:

```bash
python3 setup_heating.py --token <token>                 # boiler thermostat (climate)
python3 setup_heating.py --token <token> --source switch # on/off relay instead
```

On a fresh HA it can do onboarding as well: `python3 setup_heating.py --onboard --username test --password testtest`.

It creates "Sim heating" with all five rooms, presence `input_boolean.sim_somebody_home`, away mode off. Only one entry at a time: delete it first to switch heat source type.

## Things to try

- Set rooms to Heat and watch `sensor.sim_heating_winning_room`: the room with the biggest gap wins, its TRVs get the +1 °C buffer, `climate.sim_boiler` gets its target.
- Turn on `input_boolean.sim_living_room_garden_door`: after 30 s the living room pauses (`binary_sensor.living_room_paused`), its TRVs go to 10 °C; turn it off and it resumes.
- Turn off `input_boolean.sim_somebody_home`: all rooms pause and the CH switch goes off; turn it on and everything returns.
- Presets eco and warm on the room climates, pause/resume services, `set_all_off`.
- Restart HA: rooms come back in the same mode, preset and target.

## Tuning the simulation

In `config/configuration.yaml` under `heating_sim`: `speed`, `outdoor_temperature`, per room `start_temperature`, `trvs`, `heat_rate` (°C per hour, all valves open), `loss_rate` and `window`. Restart after changes.

## Reset

```bash
docker compose down
git clean -fdX config/   # removes the test HA's database, users and entries
```
