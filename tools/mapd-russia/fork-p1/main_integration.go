// +build ignore

// Example integration into mapd main loop.
// Add this logic to your existing main.go or tile manager.

package main

import (
	"log/slog"
	"time"

	"pfeifer.dev/mapd/settings"
)

const (
	tileUpdateInterval = 24 * time.Hour
)

// checkAndUpdateTiles checks for new GitHub release and updates tiles if needed.
// Call this periodically (e.g., once per day) in the main loop.
func checkAndUpdateTiles() {
	hasUpdate, version, err := settings.CheckReleaseUpdate()
	if err != nil {
		slog.Warn("Failed to check for tile update", "error", err)
		return
	}

	if !hasUpdate {
		slog.Debug("Tiles are up to date")
		return
	}

	slog.Info("New tile release available", "version", version)

	if err := settings.DownloadAndExtractRelease(version); err != nil {
		slog.Error("Failed to download tiles", "error", err)
		return
	}

	slog.Info("Tiles updated successfully", "version", version)
}

// Example usage in main loop:
//
// func main() {
//     ticker := time.NewTicker(tileUpdateInterval)
//     defer ticker.Stop()
//
//     for {
//         select {
//         case <-ticker.C:
//             checkAndUpdateTiles()
//         }
//     }
// }
