# Control RoboSpot Viewer
Discover and view multiple Robe RoboSpot Motion Camera RTSP streams.

## Installation
To build the executable you'll need Python (3.14.3) or later and the following Python packages:

- `opencv-python`
- `numpy`
- `WSDiscovery`

You can install these using pip:

Unix/MacOS
```
pip install opencv-python pysrt
```

Windows:
```
py -m pip install -r requirements.txt
```


## Build

```
py -m PyInstaller camera_viewer.spec
```

## Usage
- Make sure host network adapter has IP in 10.x.x.x range and subnet mask as 255.0.0.0
- Application will discover all Robe RoboSpot cameras and connect to the streams

## Roadmap
- [ ] Allow users to select network adapter to use from OS
- [x] Automatically detect when new camera/streams become available
- [x] Disconnect from lost streams
- [ ] Change grid layout to feature a stream
- [ ] Label cameras



