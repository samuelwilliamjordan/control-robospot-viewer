import cv2
import socket
import subprocess
import platform
import numpy as np
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from wsdiscovery import WSDiscovery


# -------- SETTINGS --------
RTSP_PATH = "/profile2/media.smp"
TIMEOUT = 1
MAX_THREADS = 50
FRAME_WIDTH = 1440
FRAME_HEIGHT = 720

DISCOVERY_INTERVAL = 1 # seconds between discovery runs
PING_INTERVAL = 1             
PING_LOSS_TIMEOUT = 3 # seconds before removing camera


def ping(ip):
    """Pings device to make sure it is still reachable."""
    system = platform.system().lower()

    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", "500", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]

    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except:
        return False


def check_rtsp(ip):
    """Check if RTSP port 554 is open."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((ip, 554))
        s.close()
        return True
    except:
        return False


def test_feed(ip):
    """Try opening the RTSP stream."""
    url = f"rtsp://{ip}{RTSP_PATH}"
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if cap.isOpened():
        print(f"RTSP feed found: {url}")
        cap.release()
        return url    
    return None


def scan_ip(ip):
    if check_rtsp(ip):
        return test_feed(ip)
    return None


def discover_cameras(timeout=1):
    print("Discovering devices...")

    wsd = WSDiscovery()
    wsd.start()

    services = wsd.searchServices(timeout=timeout)

    cameras = []
    for service in services:
        for addr in service.getXAddrs():
            try:
                host = addr.split("//")[1].split("/")[0]
                ip = host.split(":")[0]
                if ip.startswith("10."):
                    cameras.append(ip)
            except:
                pass

    wsd.stop()
    return list(set(cameras))


class CameraCapture(threading.Thread):
    """Camera thread that reads frames."""
    def __init__(self, url, frame_width, frame_height, fps_window_seconds=5.0):
        super().__init__(daemon=True)
        self.url = url
        self.ip = url.replace("rtsp://", "").split("/")[0]

        # Set FFmpeg timeouts to prevent blocking when camera disconnects
        try:
            self.cap = cv2.VideoCapture(
                self.url,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1000,
                 cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000]
            )
        except TypeError:
            # Fallback for OpenCV versions that don't support params
            self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

        self.frame_width = frame_width
        self.frame_height = frame_height

        self.lock = threading.Lock()
        self.latest_frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
        self.running = True

        self.fps_window_seconds = fps_window_seconds
        self.timestamps = deque()
        self.avg_fps = 0.0

    def run(self):
        while self.running:
            ret, frame = self.cap.read()

            # When asked, exit immediately
            if not self.running:
                break

            now = time.time()

            if ret:
                frame = cv2.resize(frame, (self.frame_width, self.frame_height))

                self.timestamps.append(now)
                while self.timestamps and (now - self.timestamps[0]) > self.fps_window_seconds:
                    self.timestamps.popleft()
                self.avg_fps = len(self.timestamps) / self.fps_window_seconds

                with self.lock:
                    self.latest_frame = frame
            else:
                # No frame received; short sleep to avoid busy loop
                time.sleep(0.05)

        self.cap.release()

    def get_frame_and_fps(self):
        with self.lock:
            frame = self.latest_frame.copy()
            fps = self.avg_fps
        return frame, fps

    def stop(self):
        """Graceful stop: let the loop exit after current read."""
        self.running = False

    def force_close(self):
        """Immediate stop: release FFmpeg handle so read() unblocks quickly."""
        self.running = False
        try:
            self.cap.release()
        except:
            pass




class DeviceManager(threading.Thread):
    """WS-Discovery then pings to check devices are still reachable"""
    def __init__(self, cameras, lock):
        super().__init__(daemon=True)
        self.cameras = cameras
        self.lock = lock
        self.running = True

        self.discovery_results = Queue()
        self.last_seen = {}

        # Workers
        self.discovery_thread = threading.Thread(
            target=self.discovery_worker,
            daemon=True
        )
        self.ping_pool = ThreadPoolExecutor(max_workers=20)


    def discovery_worker(self):
        while self.running:
            try:
                ips = discover_cameras(timeout=DISCOVERY_INTERVAL)
                self.discovery_results.put(ips)
            except Exception as e:
                print(f"Discovery error: {e}")
            time.sleep(DISCOVERY_INTERVAL)


    def parallel_ping(self, ips):
        futures = {self.ping_pool.submit(ping, ip): ip for ip in ips}
        results = {}
        for future in as_completed(futures):
            ip = futures[future]
            try:
                results[ip] = future.result()
            except:
                results[ip] = False
        return results


    def run(self):
        self.discovery_thread.start()

        while self.running:
            # --- Handle new discovery results ---
            while not self.discovery_results.empty():
                new_ips = self.discovery_results.get()
                self.handle_discovery(new_ips)

            # --- Parallel ping all active cameras ---
            with self.lock:
                active_ips = list(self.cameras.keys())

            ping_results = self.parallel_ping(active_ips)

            # --- Handle ping results ---
            for ip, alive in ping_results.items():
                if alive:
                    self.last_seen[ip] = time.time()
                else:
                    if time.time() - self.last_seen.get(ip, 0) > PING_LOSS_TIMEOUT:
                        self.remove_camera(ip)

            time.sleep(0.1)  # very fast loop


    def handle_discovery(self, ips):
        with self.lock:
            current = set(self.cameras.keys())

        new_ips = set(ips) - current
        for ip in new_ips:
            if check_rtsp(ip):
                url = f"rtsp://{ip}{RTSP_PATH}"
                cam = CameraCapture(url, FRAME_WIDTH, FRAME_HEIGHT)
                cam.start()

                with self.lock:
                    self.cameras[ip] = cam

                self.last_seen[ip] = time.time()
                print(f"Added new camera: {ip}")


    def remove_camera(self, ip):
        print(f"Removing camera: {ip}")
        with self.lock:
            cam = self.cameras.pop(ip, None)

        if cam:
            cam.force_close()
            cam.join(timeout=1)

        self.last_seen.pop(ip, None)

    def stop(self):
        self.running = False


def display_multiple_streams():
    cameras = {}
    lock = threading.Lock()

    # Start discovery
    manager = DeviceManager(cameras, lock)
    manager.start()

    window_name = "Control RoboSpot Viewer"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
 
    cv2.resizeWindow(window_name, FRAME_WIDTH, FRAME_HEIGHT) 
    cv2.moveWindow(window_name, 100, 100)

    output_timestamps = deque()
    OUTPUT_FPS_WINDOW = 5.0

    try:
        while True:
            loop_now = time.time()
            output_timestamps.append(loop_now)
            while output_timestamps and (loop_now - output_timestamps[0]) > OUTPUT_FPS_WINDOW:
                output_timestamps.popleft()
            output_fps = len(output_timestamps) / OUTPUT_FPS_WINDOW

            with lock:
                active_cams = list(cameras.values())

            # If no cameras show "Searching" placeholder
            if not active_cams:
                blank = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
                cv2.putText(blank, "Searching...", (50, FRAME_HEIGHT // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 255, 255), 3)
                cv2.imshow(window_name, blank)

                if cv2.waitKey(1) == 27:
                    break
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
                continue

            # Build camera grid
            frames = []
            for cam in active_cams:
                frame, input_fps = cam.get_frame_and_fps()

                cv2.putText(frame, f"Camera: {cam.ip}", (10, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

                cv2.putText(frame, f"Input FPS: {input_fps:.1f}", (10, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                cv2.putText(frame, f"Output FPS: {output_fps:.1f}", (10, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)

                frames.append(frame)

            count = len(frames)
            cols = int(np.ceil(np.sqrt(count)))
            rows = int(np.ceil(count / cols))
            grid = np.zeros((rows * FRAME_HEIGHT, cols * FRAME_WIDTH, 3), dtype=np.uint8)

            idx = 0
            for r in range(rows):
                for c in range(cols):
                    if idx < count:
                        grid[r * FRAME_HEIGHT:(r + 1) * FRAME_HEIGHT,
                             c * FRAME_WIDTH:(c + 1) * FRAME_WIDTH] = frames[idx]
                    idx += 1

            cv2.imshow(window_name, grid)

            # ESC key exits
            if cv2.waitKey(1) == 27:
                break
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

    finally:
        manager.stop()
        manager.join()

        with lock:
            for cam in cameras.values():
                cam.force_close()
            for cam in cameras.values():
                cam.join(timeout=1)

        cv2.destroyAllWindows()


def main():
    print("\nOpening viewer window...")
    display_multiple_streams()

    print("Initial discovery...")
    try:
        ips = discover_cameras(timeout=DISCOVERY_INTERVAL)
    except Exception as e:
        print(f"Discovery error: {e}")
        ips = []

    print("Found broadcasting devices:")
    for ip in ips:
        print("  ", ip)

    print("\nScanning for RTSP feeds...")

    found_urls = []

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        for result in executor.map(scan_ip, ips):
            if result:
                found_urls.append(result)


if __name__ == "__main__":
    main()
