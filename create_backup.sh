#!/bin/bash

#################################################
# Rain Alert - Backup Script
#################################################
# Creates timestamped backup of all critical data
# Run before major changes or weekly for safety
#################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Timestamp for backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_ONLY=$(date +%Y%m%d)

# Directories
BACKUP_DIR="backups"
BACKUP_NAME="rain-alert-backup-${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

echo ""
echo -e "${BLUE}🌧️  Rain Alert - Creating Backup${NC}"
echo "================================================"
echo ""

# Create backup directory if it doesn't exist
if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "${YELLOW}📁 Creating backup directory...${NC}"
    mkdir -p "$BACKUP_DIR"
fi

# Create timestamped backup directory
echo -e "${YELLOW}📦 Creating backup: ${BACKUP_NAME}${NC}"
mkdir -p "$BACKUP_PATH"

# Check if critical files exist
if [ ! -f "data/rain_alert.db" ]; then
    echo -e "${RED}❌ ERROR: Database not found at data/rain_alert.db${NC}"
    echo "   Nothing to backup!"
    exit 1
fi

# Backup database
echo -e "${YELLOW}💾 Backing up database...${NC}"
cp data/rain_alert.db "${BACKUP_PATH}/rain_alert.db"
DB_SIZE=$(du -h data/rain_alert.db | cut -f1)
echo -e "${GREEN}   ✓ Database backed up (${DB_SIZE})${NC}"

# Backup .env if exists
if [ -f ".env" ]; then
    echo -e "${YELLOW}⚙️  Backing up configuration...${NC}"
    cp .env "${BACKUP_PATH}/.env"
    echo -e "${GREEN}   ✓ Configuration backed up${NC}"
fi

# Backup radar alert images if they exist
if [ -d "data/radar/alerts" ]; then
    ALERT_COUNT=$(find data/radar/alerts -name "*.png" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$ALERT_COUNT" -gt 0 ]; then
        echo -e "${YELLOW}🖼️  Backing up ${ALERT_COUNT} alert radar images...${NC}"
        mkdir -p "${BACKUP_PATH}/radar_alerts"
        cp -r data/radar/alerts/* "${BACKUP_PATH}/radar_alerts/" 2>/dev/null || true
        RADAR_SIZE=$(du -sh "${BACKUP_PATH}/radar_alerts" | cut -f1)
        echo -e "${GREEN}   ✓ Radar images backed up (${RADAR_SIZE})${NC}"
    else
        echo -e "${YELLOW}   ℹ️  No alert radar images to backup${NC}"
    fi
fi

# Backup logs if they exist
if [ -f "rain-alert.log" ]; then
    LOG_SIZE=$(du -h rain-alert.log | cut -f1)
    if [ "$LOG_SIZE" != "0B" ]; then
        echo -e "${YELLOW}📝 Backing up logs...${NC}"
        cp rain-alert.log "${BACKUP_PATH}/rain-alert.log"
        echo -e "${GREEN}   ✓ Logs backed up (${LOG_SIZE})${NC}"
    fi
fi

# Create archive
echo ""
echo -e "${YELLOW}🗜️  Creating compressed archive...${NC}"
cd "$BACKUP_DIR"
tar -czf "${BACKUP_NAME}.tar.gz" "$BACKUP_NAME"
cd ..

# Get archive size
ARCHIVE_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1)

# Remove uncompressed directory
rm -rf "$BACKUP_PATH"

echo -e "${GREEN}   ✓ Archive created (${ARCHIVE_SIZE})${NC}"

# Summary
echo ""
echo -e "${GREEN}✅ Backup completed successfully!${NC}"
echo ""
echo "Backup location:"
echo -e "${BLUE}   ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz${NC}"
echo ""
echo "To restore this backup:"
echo -e "${YELLOW}   tar -xzf ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz${NC}"
echo -e "${YELLOW}   cp ${BACKUP_NAME}/rain_alert.db data/rain_alert.db${NC}"
echo -e "${YELLOW}   cp ${BACKUP_NAME}/.env .env${NC}"
echo -e "${YELLOW}   ./restart.sh${NC}"
echo ""

# List recent backups
echo "Recent backups:"
ls -lh "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -5 | awk '{print "   " $9 " (" $5 ")"}'
echo ""

# Cleanup old backups (keep last 10)
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/*.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 10 ]; then
    echo -e "${YELLOW}🧹 Cleaning up old backups (keeping last 10)...${NC}"
    cd "$BACKUP_DIR"
    ls -t *.tar.gz | tail -n +11 | xargs rm -f
    cd ..
    echo -e "${GREEN}   ✓ Old backups cleaned${NC}"
    echo ""
fi

# Export training data if there's feedback
FEEDBACK_COUNT=$(sqlite3 data/rain_alert.db "SELECT COUNT(*) FROM alerts WHERE user_feedback IS NOT NULL;" 2>/dev/null || echo "0")
if [ "$FEEDBACK_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}📊 Exporting training data (${FEEDBACK_COUNT} alerts with feedback)...${NC}"
    EXPORT_FILE="backups/alert_training_data_${DATE_ONLY}.csv"
    sqlite3 data/rain_alert.db -header -csv "
        SELECT
            a.id,
            a.created_at,
            a.alert_time,
            a.rain_expected_at,
            a.minutes_ahead,
            a.message,
            a.user_feedback,
            a.feedback_timestamp,
            a.radar_images_saved,
            l.latitude,
            l.longitude,
            l.address
        FROM alerts a
        JOIN locations l ON a.location_id = l.id
        WHERE a.user_feedback IS NOT NULL
        ORDER BY a.created_at DESC
    " > "$EXPORT_FILE" 2>/dev/null || echo "Failed to export"

    if [ -f "$EXPORT_FILE" ]; then
        EXPORT_SIZE=$(du -h "$EXPORT_FILE" | cut -f1)
        echo -e "${GREEN}   ✓ Training data exported to ${EXPORT_FILE} (${EXPORT_SIZE})${NC}"
        echo ""
    fi
fi

echo -e "${BLUE}Backup process complete!${NC}"
echo ""
