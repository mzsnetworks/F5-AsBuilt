#!/usr/bin/env python3
"""
Generate F5 BIG-IP As-Built documentation via iControl REST.

Features:
- Modular structure
- Credentials loaded from .env (F5_USER, F5_PASS, optional F5_VERIFY_SSL)
- Inventory of devices in YAML (default: f5_inventory.yml)
- CLI options:
    - -l / --list            : list inventory devices
    - -d / --device NAME     : run against specific device (by inventory name)
    - -f / --file FILE       : output filename (extension inferred by format)
    - --format {md,json}     : output format (Markdown or JSON)

Inventory example (f5_inventory.yml):

devices:
  - name: f5-prod-1
    host: "https://10.0.0.10"
    description: "Production F5 in DC1"
  - name: f5-qa-1
    host: "https://10.0.1.10"
    description: "QA F5 in DC2"

.env example:

F5_USER=admin
F5_PASS=supersecret
# optional, defaults to false if not set
F5_VERIFY_SSL=false
"""

import argparse
import json
import os
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests
import yaml
from dotenv import load_dotenv
from typing import Dict, List, Tuple, Optional, Any

# Disable SSL warnings if verify is False
requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
    requests.packages.urllib3.exceptions.InsecureRequestWarning  # type: ignore[attr-defined]
)


# =============================================================================
# REST client
# =============================================================================


class F5Client:
    """Simple iControl REST client for BIG-IP."""

    def __init__(
        self, host: str, username: str, password: str, verify_ssl: bool = False
    ):
        self.base_url = host.rstrip("/") + "/mgmt/"
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({"Content-Type": "application/json"})

    def get_collection(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET a collection endpoint like 'tm/ltm/virtual'. Returns list or dict."""
        url = urljoin(self.base_url, path.lstrip("/"))
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return data

    def get_object(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """GET a single object endpoint."""
        url = urljoin(self.base_url, path.lstrip("/"))
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Helpers
# =============================================================================


def parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def parse_destination(dest: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    F5 virtual 'destination' looks like: '/Common/10.1.1.10:443' or '/Common/10.1.1.10%1:443'.
    Returns (ip, port).
    """
    if not dest:
        return None, None

    # Strip partition (/Common/...)
    if dest.startswith("/"):
        parts = dest.split("/")
        dest = parts[-1]

    # Now dest should be '10.1.1.10:443' or '10.1.1.10%1:443'
    ip_part, sep, port = dest.partition(":")
    if not sep:
        # No port found
        return dest, None

    # Remove route-domain if present (10.1.1.10%1)
    ip = ip_part.split("%")[0]
    return ip, port


# =============================================================================
# Inventory handling
# =============================================================================


def load_inventory(inventory_path: str) -> Dict[str, Any]:
    if not os.path.exists(inventory_path):
        print(f"[ERROR] Inventory file not found: {inventory_path}", file=sys.stderr)
        sys.exit(1)

    with open(inventory_path, "r", encoding="utf-8") as f:
        try:
            inv = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            print(f"[ERROR] Failed to parse inventory YAML: {e}", file=sys.stderr)
            sys.exit(1)

    if "devices" not in inv or not isinstance(inv["devices"], list):
        print("[ERROR] Inventory must contain a 'devices' list.", file=sys.stderr)
        sys.exit(1)

    return inv


def list_devices(inventory: Dict[str, Any]) -> None:
    print("Available F5 devices:")
    print("---------------------")
    for dev in inventory["devices"]:
        name = dev.get("name", "<no-name>")
        host = dev.get("host", "<no-host>")
        desc = dev.get("description", "")
        line = f"- {name}: {host}"
        if desc:
            line += f"  ({desc})"
        print(line)


def get_device_by_name(inventory: Dict[str, Any], name: str) -> Dict[str, Any]:
    for dev in inventory["devices"]:
        if dev.get("name") == name:
            return dev
    print(f"[ERROR] Device '{name}' not found in inventory.", file=sys.stderr)
    sys.exit(1)


# =============================================================================
# Data collection
# =============================================================================


def collect_device_info(client: F5Client) -> Dict[str, Any]:
    # Hostname
    try:
        global_settings = client.get_object("tm/sys/global-settings")
        hostname = global_settings.get("hostname", "unknown")
    except Exception:
        hostname = "unknown"

    # Version
    try:
        version_info = client.get_object("tm/sys/version")
        entries = version_info.get("entries", {})
        full_version = "unknown"
        for v in entries.values():
            fv = (
                v.get("nestedStats", {})
                .get("entries", {})
                .get("Version", {})
                .get("description")
            )
            if fv:
                full_version = fv
                break
    except Exception:
        full_version = "unknown"

    # HA / sync group
    ha_status = "unknown"
    sync_group = "unknown"
    try:
        devices = client.get_collection("tm/cm/device")
        for d in devices:
            if d.get("selfDevice") == "true":
                ha_status = d.get("failoverState", "unknown")
                sync_group = d.get("configSyncGroup", "unknown")
                break
    except Exception:
        pass

    # Partitions
    try:
        parts = client.get_collection("tm/auth/partition")
        partitions = [p.get("name") for p in parts]
    except Exception:
        partitions = []

    return {
        "hostname": hostname,
        "version": full_version,
        "ha_status": ha_status,
        "sync_group": sync_group,
        "partitions": partitions,
    }


def collect_ltm_objects(client: F5Client) -> Dict[str, Any]:
    # Virtual servers
    virtuals_raw = client.get_collection("tm/ltm/virtual")
    virtuals: List[Dict[str, Any]] = []
    for vs in virtuals_raw:
        ip, port = parse_destination(vs.get("destination"))
        pool = vs.get("pool")
        if pool:
            pool = pool.split("/")[-1]

        profiles: List[str] = []
        try:
            prof_items = vs.get("profilesReference", {}).get("items", [])
            profiles = [p.get("name") for p in prof_items]
        except Exception:
            pass

        persistence: List[str] = []
        if "persist" in vs:
            persistence = [p.get("name") for p in vs.get("persist", [])]

        irules: List[str] = []
        if "rules" in vs:
            irules = [r.split("/")[-1] for r in vs.get("rules", [])]

        virtuals.append(
            {
                "name": vs.get("name"),
                "destination_ip": ip,
                "destination_port": port,
                "pool": pool,
                "profiles": profiles,
                "persistence": persistence,
                "irules": irules,
            }
        )

    # Pools
    pools_raw = client.get_collection(
        "tm/ltm/pool", params={"expandSubcollections": "true"}
    )
    pools: List[Dict[str, Any]] = []
    for p in pools_raw:
        members: List[Dict[str, Any]] = []
        try:
            mem_items = p.get("membersReference", {}).get("items", [])
            for m in mem_items:
                members.append(
                    {
                        "name": m.get("name"),
                        "address": m.get("address"),
                        "state": m.get("state"),
                        "session": m.get("session"),
                    }
                )
        except Exception:
            pass

        pools.append(
            {
                "name": p.get("name"),
                "lb_method": p.get("loadBalancingMode"),
                "monitor": p.get("monitor"),
                "members": members,
            }
        )

    # Nodes
    nodes_raw = client.get_collection("tm/ltm/node")
    nodes: List[Dict[str, Any]] = []
    for n in nodes_raw:
        nodes.append(
            {
                "name": n.get("name"),
                "address": n.get("address"),
                "state": n.get("state"),
                "session": n.get("session"),
            }
        )

    # iRules
    irules_raw = client.get_collection("tm/ltm/rule")
    irules: List[Dict[str, Any]] = []
    for r in irules_raw:
        irules.append(
            {
                "name": r.get("name"),
                "partition": r.get("partition", "Common"),
                "fullPath": r.get("fullPath"),
            }
        )

    # Monitors by type
    monitors: List[Dict[str, Any]] = []
    monitor_types = ["http", "https", "tcp", "gateway-icmp", "icmp"]
    for mtype in monitor_types:
        try:
            items = client.get_collection(f"tm/ltm/monitor/{mtype}")
            for m in items:
                monitors.append(
                    {
                        "name": m.get("name"),
                        "partition": m.get("partition", "Common"),
                        "type": mtype,
                        "fullPath": m.get("fullPath"),
                    }
                )
        except Exception:
            # type not present on this box, skip
            continue

    # SSL profiles (client-ssl)
    ssl_profiles: List[Dict[str, Any]] = []
    try:
        ssl_profiles_raw = client.get_collection("tm/ltm/profile/client-ssl")
    except Exception:
        ssl_profiles_raw = []

    for sp in ssl_profiles_raw:
        ssl_profiles.append(
            {
                "name": sp.get("name"),
                "partition": sp.get("partition", "Common"),
                "fullPath": sp.get("fullPath"),
                "cert": sp.get("cert"),
                "chain": sp.get("chain"),
            }
        )

    # SSL certs
    certs: List[Dict[str, Any]] = []
    try:
        certs_raw = client.get_collection("tm/sys/crypto/cert")
        for c in certs_raw:
            certs.append(
                {
                    "name": c.get("name"),
                    "partition": c.get("partition", "Common"),
                    "fullPath": c.get("fullPath"),
                    "expiration": c.get(
                        "expirationDate", c.get("expiration", "unknown")
                    ),
                }
            )
    except Exception:
        pass

    return {
        "virtuals": virtuals,
        "pools": pools,
        "nodes": nodes,
        "irules": irules,
        "monitors": monitors,
        "ssl_profiles": ssl_profiles,
        "certs": certs,
    }


# =============================================================================
# Cross-references (where-used maps)
# =============================================================================


def build_usage_maps(ltm_data: Dict[str, Any]) -> Dict[str, Any]:
    # iRules usage
    irule_usage: Dict[str, List[str]] = {}
    for vs in ltm_data["virtuals"]:
        for r in vs["irules"]:
            irule_usage.setdefault(r, []).append(vs["name"])

    # Monitor usage (by pool)
    monitor_usage: Dict[str, List[str]] = {}
    for p in ltm_data["pools"]:
        m = p.get("monitor")
        if not m:
            continue
        m_name = m.split("/")[-1]
        monitor_usage.setdefault(m_name, []).append(p["name"])

    # SSL profile usage (by virtual)
    ssl_profile_usage: Dict[str, List[str]] = {}
    for vs in ltm_data["virtuals"]:
        for prof in vs["profiles"]:
            ssl_profile_usage.setdefault(prof, []).append(vs["name"])

    # Cert usage via SSL profiles
    cert_usage: Dict[str, List[str]] = {}
    for sp in ltm_data["ssl_profiles"]:
        cert = sp.get("cert")
        if not cert:
            continue
        cert_name = cert.split("/")[-1]
        vips = ssl_profile_usage.get(sp["name"], [])
        cert_usage.setdefault(cert_name, []).extend(vips)

    return {
        "irule_usage": irule_usage,
        "monitor_usage": monitor_usage,
        "ssl_profile_usage": ssl_profile_usage,
        "cert_usage": cert_usage,
    }


# =============================================================================
# Markdown rendering (standardized sections 0–5)
# =============================================================================


def render_markdown(
    device_info: Dict[str, Any], ltm_data: Dict[str, Any], usage_maps: Dict[str, Any]
) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: List[str] = []

    lines.append(f"# F5 As-Built – {device_info['hostname']}")
    lines.append("")
    lines.append(f"_Generated on {now}_")
    lines.append("")

    # 0. Device Report
    lines.append("## 0. Device Report")
    lines.append("")
    lines.append(f"- **Hostname:** {device_info['hostname']}")
    lines.append(f"- **Software Version:** {device_info['version']}")
    lines.append(f"- **HA Status:** {device_info['ha_status']}")
    lines.append(f"- **Sync Group:** {device_info['sync_group']}")
    parts = device_info.get("partitions") or []
    lines.append(f"- **Partitions:** {', '.join(parts) if parts else 'None'}")
    lines.append("")

    # 1. Virtual Servers
    lines.append("## 1. Virtual Servers")
    lines.append("")
    for vs in sorted(ltm_data["virtuals"], key=lambda x: x["name"] or ""):
        lines.append(f"### {vs['name']}")
        lines.append("")
        lines.append(f"- **Destination IP:** `{vs['destination_ip']}`")
        lines.append(f"- **Destination Port:** `{vs['destination_port']}`")
        lines.append(f"- **Default Pool:** `{vs['pool']}`")
        profiles = vs.get("profiles") or []
        persistence = vs.get("persistence") or []
        irules = vs.get("irules") or []
        lines.append(f"- **Profiles:** {', '.join(profiles) if profiles else 'None'}")
        lines.append(
            f"- **Persistence:** {', '.join(persistence) if persistence else 'None'}"
        )
        lines.append(f"- **iRules:** {', '.join(irules) if irules else 'None'}")
        lines.append("")

    # 2. Pools
    lines.append("## 2. Pools")
    lines.append("")
    for p in sorted(ltm_data["pools"], key=lambda x: x["name"] or ""):
        lines.append(f"### {p['name']}")
        lines.append("")
        lines.append(f"- **Load Balancing Method:** `{p['lb_method']}`")
        lines.append(f"- **Monitor:** `{p['monitor']}`")
        lines.append("")
        lines.append("| Member | Address | State | Session |")
        lines.append("|--------|---------|-------|---------|")
        for m in p["members"]:
            lines.append(
                f"| `{m['name']}` | `{m['address']}` | `{m['state']}` | `{m['session']}` |"
            )
        if not p["members"]:
            lines.append("| _No members_ |  |  |  |")
        lines.append("")

    # 3. Nodes
    lines.append("## 3. Nodes")
    lines.append("")
    lines.append("| Node | IP Address | State | Session |")
    lines.append("|------|------------|-------|---------|")
    for n in sorted(ltm_data["nodes"], key=lambda x: x["name"] or ""):
        lines.append(
            f"| `{n['name']}` | `{n['address']}` | `{n['state']}` | `{n['session']}` |"
        )
    if not ltm_data["nodes"]:
        lines.append("| _No nodes_ |  |  |  |")
    lines.append("")

    # 4. Monitors & iRules
    lines.append("## 4. Monitors & iRules")
    lines.append("")

    # 4.1 Monitors
    lines.append("### 4.1 Monitors")
    lines.append("")
    for m in sorted(ltm_data["monitors"], key=lambda x: x["name"] or ""):
        used_by = usage_maps["monitor_usage"].get(m["name"], [])
        lines.append(f"#### {m['name']}")
        lines.append("")
        lines.append(f"- **Type:** `{m['type']}`")
        lines.append(f"- **Partition:** `{m['partition']}`")
        lines.append(
            f"- **Used by Pools:** {', '.join(used_by) if used_by else 'Not referenced by any pool'}"
        )
        lines.append("")

    # 4.2 iRules
    lines.append("### 4.2 iRules")
    lines.append("")
    for r in sorted(ltm_data["irules"], key=lambda x: x["name"] or ""):
        used_by = usage_maps["irule_usage"].get(r["name"], [])
        lines.append(f"#### {r['name']}")
        lines.append("")
        lines.append(f"- **Partition:** `{r['partition']}`")
        lines.append(
            f"- **Used by Virtual Servers:** {', '.join(used_by) if used_by else 'Not referenced by any virtual server'}"
        )
        lines.append("")

    # 5. SSL Profiles & Certificates
    lines.append("## 5. SSL Profiles & Certificates")
    lines.append("")

    # 5.1 SSL profiles (with attached VIPs)
    lines.append("### 5.1 SSL Profiles")
    lines.append("")
    for sp in sorted(ltm_data["ssl_profiles"], key=lambda x: x["name"] or ""):
        used_by = usage_maps["ssl_profile_usage"].get(sp["name"], [])
        lines.append(f"#### {sp['name']}")
        lines.append("")
        lines.append(f"- **Partition:** `{sp['partition']}`")
        lines.append(f"- **Certificate:** `{sp['cert']}`")
        lines.append(f"- **Chain:** `{sp['chain']}`")
        lines.append(
            f"- **Used by Virtual Servers:** {', '.join(used_by) if used_by else 'Not referenced by any virtual server'}"
        )
        lines.append("")

    # 5.2 Certificates (with attached VIPs via profiles)
    lines.append("### 5.2 Certificates")
    lines.append("")
    for c in sorted(ltm_data["certs"], key=lambda x: x["name"] or ""):
        used_by = usage_maps["cert_usage"].get(c["name"], [])
        lines.append(f"#### {c['name']}")
        lines.append("")
        lines.append(f"- **Partition:** `{c['partition']}`")
        lines.append(f"- **Full Path:** `{c['fullPath']}`")
        lines.append(f"- **Expiration:** `{c['expiration']}`")
        lines.append(
            f"- **Used by Virtual Servers (via SSL profiles):** "
            f"{', '.join(used_by) if used_by else 'Not referenced'}"
        )
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# CLI / Orchestration
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F5 As-Built generator")
    parser.add_argument(
        "-i",
        "--inventory",
        default="f5_inventory.yml",
        help="Path to YAML inventory file (default: f5_inventory.yml)",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List devices in inventory and exit",
    )
    parser.add_argument(
        "-d",
        "--device",
        help="Device name from inventory to run as-built against",
    )
    parser.add_argument(
        "-f",
        "--file",
        help="Output filename (default: f5_<device>_asbuilt.<ext>)",
    )
    parser.add_argument(
        "--format",
        choices=["md", "json"],
        default="md",
        help="Output format: md (Markdown) or json (structured). Default: md",
    )
    return parser.parse_args()


def ensure_credentials_from_env() -> Tuple[str, str, bool]:
    load_dotenv()
    user = os.getenv("F5_USER")
    password = os.getenv("F5_PASS")
    if not user or not password:
        print(
            "[ERROR] F5_USER and F5_PASS must be set in the environment or .env",
            file=sys.stderr,
        )
        sys.exit(1)
    verify_ssl = parse_bool_env(os.getenv("F5_VERIFY_SSL"), default=False)
    return user, password, verify_ssl


def gather_asbuilt(
    device: Dict[str, Any], username: str, password: str, verify_ssl: bool
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    host = device.get("host")
    if not host:
        print(
            f"[ERROR] Device '{device.get('name')}' is missing 'host' in inventory.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = F5Client(
        host=host, username=username, password=password, verify_ssl=verify_ssl
    )

    try:
        device_info = collect_device_info(client)
        ltm_data = collect_ltm_objects(client)
        usage_maps = build_usage_maps(ltm_data)
    except requests.HTTPError as e:
        print(f"[ERROR] HTTP error from F5 {host}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Unexpected error from F5 {host}: {e}", file=sys.stderr)
        sys.exit(1)

    return device_info, ltm_data, usage_maps


def write_output(
    device: Dict[str, Any],
    device_info: Dict[str, Any],
    ltm_data: Dict[str, Any],
    usage_maps: Dict[str, Any],
    output_file: str,
    output_format: str,
    custom_path: bool,
) -> None:
    """
    Writes output to Markdown or JSON and stores files in format-specific folders,
    unless a custom -f path was explicitly provided by the user.
    """
    # Ensure default folder if user did NOT supply -f
    # (We know this because custom_path will be False when the filename is auto-generated)
    if not custom_path:
        if output_format == "md":
            out_dir = "markdown"
        elif output_format == "json":
            out_dir = "json"
        else:
            out_dir = "."  # fallback just in case

        os.makedirs(out_dir, exist_ok=True)
        output_file = os.path.join(out_dir, output_file)

    if output_format == "md":
        content = render_markdown(device_info, ltm_data, usage_maps)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Wrote Markdown as-built for {device.get('name')} to: {output_file}")

    else:  # json
        payload = {
            "device_report": device_info,  # 0
            "virtual_servers": ltm_data["virtuals"],  # 1
            "pools": ltm_data["pools"],  # 2
            "nodes": ltm_data["nodes"],  # 3
            "monitors": ltm_data["monitors"],  # 4 (part 1)
            "irules": ltm_data["irules"],  # 4 (part 2)
            "ssl_profiles": ltm_data["ssl_profiles"],  # 5 (profiles)
            "certificates": ltm_data["certs"],  # 5 (certs)
            "usage": usage_maps,  # cross-refs
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote JSON as-built for {device.get('name')} to: {output_file}")


def main() -> None:
    args = parse_args()
    inventory = load_inventory(args.inventory)

    if args.list:
        list_devices(inventory)
        return

    if not args.device:
        print(
            "[ERROR] You must specify a device with -d <name> or use -l to list devices.",
            file=sys.stderr,
        )
        sys.exit(1)

    device = get_device_by_name(inventory, args.device)
    username, password, verify_ssl = ensure_credentials_from_env()

    # Determine default filename based on format if not provided
    if args.file:
        output_file = args.file
    else:
        safe_name = device.get("name", "f5").replace(" ", "_")
        ext = "json" if args.format == "json" else "md"
        output_file = f"f5_{safe_name}_asbuilt.{ext}"

    device_info, ltm_data, usage_maps = gather_asbuilt(
        device, username, password, verify_ssl
    )
    write_output(
        device,
        device_info,
        ltm_data,
        usage_maps,
        output_file,
        args.format,
        args.file is not None,  # True if user provided -f
    )


if __name__ == "__main__":
    main()
