# F5 As-Built Generator

This toolkit generates standardized **As-Built documentation** for F5 BIG‑IP devices using iControl REST.

You get three types of outputs:

- **Markdown report** – human‑readable, structured by sections.
- **JSON export** – machine‑readable, stable schema for automation.
- **Excel workbook** – one sheet per object type (VS, pools, nodes, monitors, iRules, SSL profiles).

---

## 1. Repository Layout

Recommended layout:

```text
F5-AsBuilt/
├─ f5_asbuilt.py          # Connects to F5, pulls config via iControl REST, generates MD/JSON
├─ f5_asbuilt_xls.py      # Converts JSON → Excel workbook
├─ f5_inventory.yml       # Device inventory (name/host/description)
├─ .env                   # Credentials (username, password, SSL verify)
├─ markdown/              # Auto-generated Markdown reports
├─ json/                  # Auto-generated JSON exports
└─ xls/                   # Auto-generated Excel workbooks
```

Only `f5_asbuilt.py` talks to the F5. The XLS script is offline and only consumes the JSON file.

---

## 2. Requirements

### 2.1 Python & packages

Tested with **Python 3.11**.

Install dependencies (in your venv):

```bash
pip install requests pyyaml python-dotenv openpyxl
```

- `f5_asbuilt.py` uses: `requests`, `pyyaml`, `python-dotenv`.
- `f5_asbuilt_xls.py` uses: `openpyxl`.

---

## 3. Configuration

### 3.1 Credentials – `.env`

Create a `.env` file in the repo root:

```env
F5_USER=admin
F5_PASS=supersecret
F5_VERIFY_SSL=false
```

- `F5_USER` / `F5_PASS`: iControl REST credentials.
- `F5_VERIFY_SSL`:
  - `false` → do **not** verify SSL certificates (typical for self‑signed mgmt certs).
  - `true` → verify SSL certificates.

`f5_asbuilt.py` will `load_dotenv()` and read these values automatically.

---

### 3.2 Inventory – `f5_inventory.yml`

Example:

```yaml
devices:
  - name: FLL2BLBI07V
    host: "https://10.0.0.10"
    description: "FLL2 Prod BIG-IP 01"
  - name: FLL2BLBI08V
    host: "https://10.0.0.11"
    description: "FLL2 Prod BIG-IP 02"
```

- `name`  – logical name used with `-d`.
- `host`  – full URL to the BIG‑IP (must include `https://`).
- `description` – free text, only used when listing devices.

---

## 4. Generating As‑Built (Markdown / JSON)

All of this is done with **`f5_asbuilt.py`**.

> Tip: activate your venv first, e.g. `source venv/bin/activate` or equivalent.

### 4.1 List available devices

```bash
python f5_asbuilt.py -l
```

This reads `f5_inventory.yml` and prints each device with its host and description.

---

### 4.2 Generate Markdown report

```bash
python f5_asbuilt.py -d FLL2BLBI07V
```

- Uses:
  - inventory device with `name: FLL2BLBI07V`
  - credentials from `.env`.
- Default output filename (auto‑generated):  
  `f5_FLL2BLBI07V_asbuilt.md`
- Default location (when you **don’t** pass `-f`):  
  `./markdown/f5_FLL2BLBI07V_asbuilt.md`  
  (directory is auto‑created if missing)

You can override the filename and path:

```bash
python f5_asbuilt.py -d FLL2BLBI07V -f /tmp/FLL2_asbuilt.md
```

In that case the script writes **exactly** to `/tmp/FLL2_asbuilt.md` and does **not** force the `markdown/` folder.

#### Markdown structure

The Markdown report is standardized into the following sections:

0. **Device Report**  
   - Hostname  
   - Software version  
   - HA status  
   - Sync group  
   - Partitions

1. **Virtual Servers**  
   - Name  
   - Destination IP / Port  
   - Default pool  
   - Profiles  
   - Persistence  
   - iRules

2. **Pools**  
   - Pool name  
   - Load‑balancing method  
   - Monitor  
   - Members (name, IP, state, session)

3. **Nodes**  
   - Node name  
   - IP address  
   - State / session

4. **Monitors & iRules**  
   - Monitors: name, type, partition, **used by pools**  
   - iRules: name, partition, **used by virtual servers**

5. **SSL Profiles & Certificates**  
   - SSL profiles: name, partition, cert, chain, **VIPs that use the profile**  
   - Certificates: name, partition, expiration, **VIPs that indirectly use the cert (via profiles)**

---

### 4.3 Generate JSON export

```bash
python f5_asbuilt.py -d FLL2BLBI07V --format json
```

- Default output filename:  
  `f5_FLL2BLBI07V_asbuilt.json`
- Default location (when you **don’t** pass `-f`):  
  `./json/f5_FLL2BLBI07V_asbuilt.json`

Again, you can override the filename/path:

```bash
python f5_asbuilt.py -d FLL2BLBI07V --format json -f /tmp/FLL2_asbuilt.json
```

#### JSON schema (high‑level)

The top‑level JSON object looks like:

```json
{
  "device_report": { ... },
  "virtual_servers": [ ... ],
  "pools": [ ... ],
  "nodes": [ ... ],
  "monitors": [ ... ],
  "irules": [ ... ],
  "ssl_profiles": [ ... ],
  "certificates": [ ... ],
  "usage": {
    "irule_usage": { "irule_name": ["vip1", "vip2"] },
    "monitor_usage": { "monitor_name": ["pool1", "pool2"] },
    "ssl_profile_usage": { "ssl_profile_name": ["vip1", "vip3"] },
    "cert_usage": { "cert_name": ["vip1", "vip4"] }
  }
}
```

Each list element corresponds to the objects described in the Markdown sections (same logical model, just structured as JSON).

---

## 5. Generating Excel (XLSX)

Excel export is done with **`f5_asbuilt_xls.py`** and uses the JSON file as input.

### 5.1 Basic usage

```bash
python f5_asbuilt_xls.py f5_FLL2BLBI07V_asbuilt.json
```

Resolution rules for the JSON input:

1. If the path you pass exists as‑is, it’s used directly.
2. If not, the script will automatically look for the file under `./json/`:
   - e.g. `f5_FLL2BLBI07V_asbuilt.json` → `json/f5_FLL2BLBI07V_asbuilt.json`

### 5.2 Output location

If you **don’t** pass `-o`, output goes to `./xls`:

- Input: `json/f5_FLL2BLBI07V_asbuilt.json`  
- Output: `xls/f5_FLL2BLBI07V_asbuilt.xlsx`

The `xls/` directory is created if it doesn’t exist.

You can override the output path:

```bash
python f5_asbuilt_xls.py f5_FLL2BLBI07V_asbuilt.json -o /tmp/FLL2_LB_inventory.xlsx
```

### 5.3 Excel sheets

The workbook contains one sheet per logical object type:

1. **Virtual_Servers**
   - Columns:
     - `Name`
     - `IP`
     - `Port`
     - `Pool`
     - `Profiles` (comma‑separated list)
     - `Persistence` (comma‑separated list)
     - `iRules` (comma‑separated list)

2. **Pools**
   - Columns:
     - `Pool_Name`
     - `LB_Method`
     - `Monitor`
     - `Member_Name`
     - `Member_Address`
     - `Member_State`
     - `Member_Session`
   - One row per **pool member**. Pools with no members still appear with empty member columns.

3. **Nodes**
   - Columns:
     - `Node_Name`
     - `IP_Address`
     - `State`
     - `Session`

4. **Monitors**
   - Columns:
     - `Monitor_Name`
     - `Type`
     - `Partition`
     - `Used_By_Pools` (comma‑separated pool names)

5. **IRules**
   - Columns:
     - `IRule_Name`
     - `Partition`
     - `Used_By_Virtual_Servers` (comma‑separated VIP names)

6. **SSL_Profiles**
   - Columns:
     - `Profile_Name`
     - `Partition`
     - `Certificate` (full path as reported on BIG‑IP)
     - `Certificate_Expiration` (when available from the cert object)
     - `Attached_Virtual_Servers` (comma‑separated VIP names using that profile)

This layout is designed to make it easy to filter/sort in Excel and to drive future automation (e.g., conditional formatting, compliance checks, or diffs between devices).

---

## 6. Typical Workflow

1. **Check inventory & connectivity**

   ```bash
   python f5_asbuilt.py -l
   ```

2. **Generate Markdown & JSON**

   ```bash
   # Human-readable report
   python f5_asbuilt.py -d FLL2BLBI07V

   # JSON for automation / XLS
   python f5_asbuilt.py -d FLL2BLBI07V --format json
   ```

3. **Generate Excel from JSON**

   ```bash
   python f5_asbuilt_xls.py f5_FLL2BLBI07V_asbuilt.json
   ```

4. Open:
   - `markdown/…` for documentation
   - `json/…` if you want to parse / diff programmatically
   - `xls/…` for Excel‑based inventory / reporting

---

## 7. Notes & Future Ideas

Some ideas you can add later without changing the overall design:

- `--partition` filter (only include selected partitions).
- `--vs-pattern` (filter virtual servers by name prefix/regex).
- Conditional formatting in Excel (green for **up**, red for **down**).
- A wrapper script that runs:
  - Markdown
  - JSON
  - XLS
  in one go per device.

For now, the current setup gives you a predictable, repeatable pipeline:

**F5 → JSON → Markdown / Excel** with a stable schema and clear outputs.
