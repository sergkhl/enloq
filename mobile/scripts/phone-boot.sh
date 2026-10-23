#!/system/bin/sh
# The development phone's Magisk boot script, which mobile/scripts/phone-adb
# boot installs as /data/adb/service.d/enloq-tunnel.sh. persist.adb.tcp.port
# brings network adb back after a restart, which this checks. WireGuard
# restores the tunnel it ran before the restart whenever its app starts, which
# the boot does through its quick settings tile; if nothing has, this starts
# its VPN service the way Always-on VPN does. A start fails if the tunnel's
# server name doesn't resolve yet, and WireGuard then forgets the tunnel, so
# mighty02's endpoint is the VPS's address (docs/mobile.md). What it finds
# goes to /data/local/tmp/enloq-boot.log.
address=10.8.0.3
log=/data/local/tmp/enloq-boot.log
note() { echo "$(date '+%F %T') $*" >>"$log"; }
tunnel() { ip -4 addr show | grep -q "inet $address/"; }

until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 5; done
# Port 5555 is 15B3 in /proc/net, listening is state 0A.
if grep -qE ':15B3 [0-9A-F]+:0000 0A' /proc/net/tcp /proc/net/tcp6; then
  note "adb listens on 5555"
else
  note "adb: starting it on 5555"
  setprop service.adb.tcp.port 5555
  stop adbd
  start adbd
fi
for try in 1 2 3; do
  if tunnel; then
    note "tunnel up at $address"
    exit 0
  fi
  note "tunnel: starting WireGuard's VPN service ($try)"
  am startservice -a android.net.VpnService -p com.wireguard.android >/dev/null 2>&1
  sleep 20
done
if tunnel; then note "tunnel up at $address"; else note "tunnel still down"; fi
