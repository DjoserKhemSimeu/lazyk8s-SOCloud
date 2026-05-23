import subprocess
import sys
import threading
import time
import curses
import os
from datetime import datetime, timezone

# --- Configuration & Data Management ---
node_data = {} 
lock = threading.Lock()

METRICS = [
    "rapl_consumed_energy", "cpu_percent", "memory_usage",
    "cgroup_memory_anonymous", "cgroup_memory_file",
    "cgroup_memory_kernel_stack", "cgroup_memory_pagetables",
    "nvml_instant_power", "nvml_temperature_gpu", 
    "nvml_gpu_utilization", "nvml_memory_utilization"
]

def parse_ts(ts_str):
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '')).replace(tzinfo=timezone.utc).timestamp()
    except: return time.time()

def stream_data(pod_name, node_name):
    cmd = ["kubectl", "exec", "-i", pod_name, "--", "stdbuf", "-oL", "tail", "-f", "/tmp/energy_data.csv"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    
    last_seen_rapl = {}

    while True:
        line = process.stdout.readline()
        #if line:
            #print(f"DEBUG: reçu pour {node_name} -> {line[:50]}") # Ajoute ça
        if not line: break
        line = line.strip()
        if ";" not in line: continue
        
        parts = line.split(";")
        if len(parts) < 3: continue
        
        m_name, ts_raw, val_raw = parts[0], parts[1], parts[2]
        try: val = float(val_raw)
        except: continue
        curr_ts = parse_ts(ts_raw)
        
        with lock:
            if node_name not in node_data:
                node_data[node_name] = {m: {'vals': [], 'curr': 0.0} for m in METRICS}
                node_data[node_name]['pods_cpu_usage'] = {} 
                node_data[node_name]['has_nvml'] = False
                node_data[node_name]['gpus'] = {} # Dict pour stocker les data par GPU ID

            # --- 1. ÉNERGIE CPU (RAPL) ---
            if "rapl_consumed_energy" in m_name and "domain=package_total" in line:
                m_id = f"{node_name}_package_total"
                if m_id in last_seen_rapl:
                    p_ts, _ = last_seen_rapl[m_id]
                    dt = curr_ts - p_ts
                    
                    # Si Alumet envoie des deltas (ce qui semble être le cas ici), 
                    # 'val' est déjà l'énergie consommée sur la période.
                    if 0.001 < dt < 5.0:
                        watts = val / dt  # On divise directement le delta par le temps
                        if 0 <= watts < 1000.0:
                            node_data[node_name]['rapl_consumed_energy']['curr'] = watts
                            node_data[node_name]['rapl_consumed_energy']['vals'].append(watts)
                
                last_seen_rapl[m_id] = (curr_ts, val)
            # --- 2. GPU NVIDIA (Multi-GPU Fix) ---
            elif "nvml_" in m_name:
                node_data[node_name]['has_nvml'] = True
                # On extrait l'ID du GPU (PCI bus ex: 0000:81:00.0) qui est en 5ème position
                gpu_id = parts[4] if len(parts) > 4 else "0"
                
                if gpu_id not in node_data[node_name]['gpus']:
                    node_data[node_name]['gpus'][gpu_id] = {m: {'vals': [], 'curr': 0.0} for m in METRICS if "nvml" in m}
                
                f_val = val / 1000.0 if "power" in m_name else val
                for m_key in METRICS:
                    if m_key in m_name:
                        node_data[node_name]['gpus'][gpu_id][m_key]['curr'] = f_val
                        node_data[node_name]['gpus'][gpu_id][m_key]['vals'].append(f_val)
                        if len(node_data[node_name]['gpus'][gpu_id][m_key]['vals']) > 100:
                            node_data[node_name]['gpus'][gpu_id][m_key]['vals'].pop(0)
                        break

            # --- 3. PODS CPU ---
            elif "cpu_percent" in m_name and "kind=total" in line:
                try:
                    p_name = line.split("name=")[1].split(",")[0]
                    node_data[node_name]['pods_cpu_usage'][p_name] = val
                except: pass            

            # --- 4. MÉMOIRE ---
            else:
                for m_key in ["memory_usage", "cgroup_memory_anonymous", "cgroup_memory_file", "cgroup_memory_kernel_stack", "cgroup_memory_pagetables"]:
                    if m_name.startswith(m_key):
                        final_v = val / (1024**2) 
                        node_data[node_name][m_key]['curr'] = final_v
                        node_data[node_name][m_key]['vals'].append(final_v)
                        break
            
            # Nettoyage historique noeud
            for m in node_data[node_name]:
                if isinstance(node_data[node_name][m], dict) and 'vals' in node_data[node_name][m]:
                    if len(node_data[node_name][m]['vals']) > 100:
                        node_data[node_name][m]['vals'].pop(0)

# --- UI Helpers ---
def draw_robust_box(stdscr, y, x, h, w, title, color_pair):
    try:
        stdscr.attron(color_pair)
        stdscr.hline(y, x, curses.ACS_HLINE, w); stdscr.hline(y + h - 1, x, curses.ACS_HLINE, w)
        stdscr.vline(y, x, curses.ACS_VLINE, h); stdscr.vline(y, x + w - 1, curses.ACS_VLINE, h)
        stdscr.addch(y, x, curses.ACS_ULCORNER); stdscr.addch(y, x + w - 1, curses.ACS_URCORNER)
        stdscr.addch(y + h - 1, x, curses.ACS_LLCORNER); stdscr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER)
        if title: stdscr.addstr(y, x + 2, f" {title} ", curses.A_REVERSE)
        stdscr.attroff(color_pair)
    except: pass

def draw_gauge(stdscr, y, x, w, val, max_val, color_pair):
    width = max(0, w - 2)
    filled = min(int((val / max_val) * width), width) if max_val > 0 else 0
    bar = "█" * filled + " " * (width - filled)
    try: stdscr.addstr(y, x, f"[{bar}]", color_pair)
    except: pass

def draw_line_plot(stdscr, y, x, w, h, vals, color_pair):
    if not vals or w <= 2: return
    points = vals[-w:]
    v_max = max(points) if max(points) > 0 else 1
    v_min = min(points)
    for i, v in enumerate(points):
        norm_v = int(((v - v_min) / (v_max - v_min if v_max != v_min else 1)) * (h - 1))
        try: stdscr.addch(y + h - 1 - norm_v, x + i, "⠒", color_pair | curses.A_BOLD)
        except: pass

def main(stdscr):
    curses.curs_set(0); stdscr.nodelay(True)
    if curses.has_colors():
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)   
        curses.init_pair(2, curses.COLOR_GREEN, -1)  
        curses.init_pair(3, curses.COLOR_YELLOW, -1) 
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)
        curses.init_pair(5, curses.COLOR_RED, -1)

    while True:
        stdscr.erase()
        sh, sw = stdscr.getmaxyx()
        with lock: nodes = sorted(node_data.keys())
        
        if not nodes:
            stdscr.addstr(sh//2, sw//2 - 15, "WAITING FOR ALUMET DATA...", curses.A_BLINK)
        
        y_cursor = 1
        for node in nodes:
            gpu_list = node_data[node].get('gpus', {})
            num_gpus = len(gpu_list)
            # Hauteur dynamique : 22 (base) + 9 par GPU
            node_h = 22 + (num_gpus * 9 if num_gpus > 0 else 0)
            if y_cursor + node_h > sh: break
            
            draw_robust_box(stdscr, y_cursor, 0, node_h, sw, f"NODE: {node}", curses.color_pair(1))

            # --- POWER ---
            p_w = (sw // 2) - 4
            draw_robust_box(stdscr, y_cursor + 1, 2, 9, p_w, "TOTAL POWER (RAPL)", curses.color_pair(2))
            curr_p = node_data[node]['rapl_consumed_energy']['curr']
            stdscr.addstr(y_cursor + 2, 4, f"Power: {curr_p:>6.2f} W")
            draw_gauge(stdscr, y_cursor + 3, 4, p_w - 4, curr_p, 300, curses.color_pair(2))
            draw_line_plot(stdscr, y_cursor + 5, 4, p_w - 6, 3, node_data[node]['rapl_consumed_energy']['vals'], curses.color_pair(2))

            # --- PODS ---
            cpu_w = sw - p_w - 6
            draw_robust_box(stdscr, y_cursor + 1, p_w + 3, 9, cpu_w, "TOP PODS (CPU%)", curses.color_pair(3))
            pods = sorted(node_data[node]['pods_cpu_usage'].items(), key=lambda x: x[1], reverse=True)[:6]
            for i, (p_name, p_val) in enumerate(pods):
                stdscr.addstr(y_cursor + 2 + i, p_w + 5, f"{p_name[:15]:15}")
                draw_gauge(stdscr, y_cursor + 2 + i, p_w + 21, cpu_w - 30, p_val, 100.0, curses.color_pair(3))
                stdscr.addstr(y_cursor + 2 + i, sw - 8, f"{p_val:>5.1f}%")

            # --- MULTI GPU SECTION ---
            curr_offset_y = 11
            if num_gpus > 0:
                for g_id in sorted(gpu_list.keys()):
                    draw_robust_box(stdscr, y_cursor + curr_offset_y, 2, 9, sw - 4, f"GPU ID: {g_id[-12:]}", curses.color_pair(5))
                    gpu_metrics = [
                        ("PWR", "nvml_instant_power", 300, "W"),
                        ("TMP", "nvml_temperature_gpu", 100, "C"),
                        ("GPU", "nvml_gpu_utilization", 100, "%"),
                        ("MEM", "nvml_memory_utilization", 100, "%")
                    ]
                    col_gpu_w = (sw - 10) // 4
                    for i, (lab, k, m_max, u) in enumerate(gpu_metrics):
                        col_x = 4 + (i * col_gpu_w)
                        curr_v = gpu_list[g_id][k]['curr']
                        stdscr.addstr(y_cursor + curr_offset_y + 1, col_x, f"{lab}: {curr_v:>5.1f}{u}")
                        draw_gauge(stdscr, y_cursor + curr_offset_y + 2, col_x, col_gpu_w - 4, curr_v, m_max, curses.color_pair(5))
                        draw_line_plot(stdscr, y_cursor + curr_offset_y + 4, col_x, col_gpu_w - 6, 3, gpu_list[g_id][k]['vals'], curses.color_pair(5))
                    curr_offset_y += 9

            # --- MEMORY ---
            draw_robust_box(stdscr, y_cursor + curr_offset_y, 2, 8, sw - 4, "DETAILED MEMORY (MiB)", curses.color_pair(4))
            mem_to_show = ["cgroup_memory_anonymous", "cgroup_memory_file", "cgroup_memory_kernel_stack", "cgroup_memory_pagetables"]
            for i, m in enumerate(mem_to_show):
                col_w = (sw - 10) // 4
                col_x = 4 + (i * col_w)
                curr_v = node_data[node][m]['curr']
                stdscr.addstr(y_cursor + curr_offset_y + 1, col_x, f"{m[14:].upper()}")
                stdscr.addstr(y_cursor + curr_offset_y + 2, col_x, f"{curr_v:>8.2f} MiB", curses.A_BOLD)
                draw_line_plot(stdscr, y_cursor + curr_offset_y + 4, col_x, col_w - 4, 3, node_data[node][m]['vals'], curses.color_pair(4))

            y_cursor += node_h
        stdscr.refresh()
        if stdscr.getch() == ord('q'): break
        time.sleep(0.5)

if __name__ == "__main__":
    cmd = "kubectl get pods -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName --no-headers"
    try:
        output = subprocess.check_output(cmd.split(), text=True, stderr=subprocess.DEVNULL).strip()
        for line in output.split('\n'):
            if "alumet-relay-client" in line:
                p_name, n_name = line.split()
                threading.Thread(target=stream_data, args=(p_name, n_name), daemon=True).start()
        curses.wrapper(main)
    except Exception as e: print(f"Error: {e}")
