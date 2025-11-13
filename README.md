# Nomad Transcoding Manager (Prototype)

This is a **prototype** system designed to automate the process of transcoding and organizing media files for the Nomad portable media server.  
While not yet ready for general testing, it provides a foundation for a future cross-platform tool that simplifies media preparation and syncing for Nomad.

---

## Prototype Notice
This project is currently in **early development** and is **not production-ready**.  
Over the next few days, this repository will be cleaned up and prepared for initial public testing.

---

## Purpose
The Nomad Transcoding Manager is intended to help users automatically downcode, organize, and prepare their media libraries for use with **Nomad**.  

Instead of manually transcoding, sorting, and getting covers this tool automates:
- **transcoding to Nomad-compatible formats / layout / optimized files**
- **Cover image fetching**

Ultimately, the goal is to evolve this project into a **hosted service** similar to Radarr or Sonarr, one that can run with a **Jellyfin** or **Plex** server and automatically manage syncing with a Nomad device’s SD card.  
This approach also provides backups of your Nomad content by storing the full content on your host system as well.

---

## How It Works
Once configured, the system:
1. Scans your input folder for video files.  
2. Transcodes them into a Nomad-compatible format.  
3. Organizes the output into the correct folder structure.  
4. Fetches and saves cover images (stored in the `/output` root next to show folders).  
5. Continues to monitor and process new files automatically until stopped (in default mode).

---

## CLI Flags and Modes

You can customize behavior with command-line flags:

| Flag | Description |
|------|--------------|
| `--single` | Runs in **single-fire mode**. Asks for confirmation before each file and allows skipping. |
| `--no-image` | Disables image fetching. |
| `--image-only` | Runs **only image fetching**, no transcoding or file moves. Images are saved to the current working directory or start location. |
| `--auto` | Default continuous mode. Automatically processes all files until stopped. |
| `--output [path]` | Sets a custom output directory. Defaults to `/output`. |
| `--input [path]` | Sets the input directory to scan for new media. |

---

## Planned Features
- Web UI similar to **Radarr/Sonarr**
- Web hostable through Docker (like Radarr/Sonarr)
- Jellyfin integration for library veiwing (add/remove files from the trancode que in a visual way) 
- Remote management
- Built-in health checks for SD card stability and sync verification

---

## Installation and Usage (Coming Soon)

Ill get to this... uhhh... when I do lol
