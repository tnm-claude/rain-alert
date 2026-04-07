# Future Features - Rain Alert

**Last Updated:** 2026-03-24
**Status:** Planning Only

---

## Mobile UI Mode

### Overview
Create a responsive mobile-friendly interface that adapts to small screens while maintaining the Windows 98 aesthetic.

### Key Requirements

1. **Responsive Breakpoints**
   - Desktop: 768px and above (current Windows 98 UI)
   - Mobile: Below 768px (simplified, vertical layout)

2. **Mobile Layout Changes**

   **Header/Title Bar**
   - Reduce title bar height to 32px
   - Simplified title text
   - Single close button (no minimize/maximize)

   **Radar Map Section**
   - Full-width map
   - Reduced height: 300px (instead of 500px)
   - Simplified controls:
     - Single row: [Play/Pause] [<<] [>>] [Speed dropdown]
     - Slider below controls (full width)
   - Touch-friendly buttons (min 44px tap target)

   **Alerts Section**
   - Stack vertically (no side-by-side layout)
   - Show max 3 alerts on mobile (scroll for more)
   - Larger dismiss buttons
   - Alert message wrapped to multiple lines

   **Locations Section**
   - Stack location cards vertically
   - Show one location per row
   - Remove buttons at right side
   - Add button at bottom of each card

   **Action Buttons**
   - Stack vertically (no horizontal button group)
   - Full-width buttons (100%)
   - Larger touch targets

3. **CSS Implementation Strategy**

   **Add new breakpoint in windows98.css:**
   ```css
   /* Mobile styles */
   @media (max-width: 767px) {
       body {
           padding: 10px;
       }

       .window {
           height: calc(100vh - 20px);
           max-width: 100%;
       }

       .title-bar {
           padding: 2px;
       }

       .title-bar-text {
           font-size: 12px;
       }

       /* Radar map */
       #radar-map {
           height: 300px !important;
       }

       /* Radar controls */
       #radar-viewer > div:last-child {
           flex-wrap: wrap;
       }

       .button {
           min-width: 60px;
           padding: 8px 12px;
       }

       /* Alerts */
       .alert-item {
           flex-direction: column;
           align-items: flex-start;
           gap: 8px;
       }

       .alert-item .button {
           width: 100%;
       }

       /* Locations */
       .location-item {
           flex-direction: column;
           align-items: flex-start;
           gap: 8px;
       }

       .location-item > div {
           text-align: left;
       }

       .location-item .button {
           width: 100%;
       }

       /* Button groups */
       .button-group {
           flex-direction: column;
           width: 100%;
       }

       .button-group .button {
           width: 100%;
           min-width: 100%;
       }

       /* Modal dialogs */
       .modal-dialog {
           width: calc(100vw - 40px) !important;
           max-width: 400px;
       }
   }
   ```

4. **Touch Interactions**
   - All buttons minimum 44x44px for touch targets
   - Prevent zoom on input focus (viewport meta tag)
   - Enable touch gestures for map pan/zoom
   - Swipe support for radar timeline

5. **Performance Considerations**
   - Reduce radar frame count on mobile (6 frames instead of 12)
   - Lower radar tile resolution on mobile networks
   - Lazy load location cards if many locations
   - Debounce window resize events

6. **Testing Requirements**
   - Test on iOS Safari (iPhone)
   - Test on Android Chrome
   - Test on iPad (tablet view)
   - Test landscape and portrait orientations
   - Test with 3G throttling

### Implementation Steps

1. **Phase 1: CSS Responsive Layout**
   - Add media queries to windows98.css
   - Test existing functionality at mobile breakpoints
   - Adjust spacing, padding, font sizes

2. **Phase 2: Touch Optimization**
   - Increase button tap targets
   - Add touch event handlers for map
   - Optimize radar controls for touch

3. **Phase 3: Performance**
   - Add conditional loading for mobile
   - Reduce radar data on mobile
   - Optimize image sizes

4. **Phase 4: Testing & Polish**
   - Cross-browser testing
   - Fix edge cases
   - Add viewport meta tag
   - Test with real mobile devices

### Files to Modify

- `app/static/css/windows98.css` - Add mobile breakpoints
- `app/templates/base.html` - Add viewport meta tag
- `app/templates/index.html` - Conditional radar frame loading
- `app/static/js/radar.js` (if created) - Touch event handlers

### Viewport Meta Tag
Add to `base.html` head section:
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
```

### Risk Assessment

**Low Risk:**
- CSS-only changes
- Progressive enhancement approach
- Desktop functionality unchanged

**Medium Risk:**
- Touch event handlers may conflict with existing code
- Map library touch support needs testing
- Performance on older mobile devices

**Mitigation:**
- Test thoroughly on multiple devices
- Use feature detection for touch events
- Provide fallback for older browsers
- Monitor performance metrics

### Success Criteria

- UI renders correctly on screens 320px - 767px wide
- All buttons are touch-friendly (44x44px minimum)
- Map is interactive on touch devices
- No horizontal scrolling on any mobile device
- Alert dismissal works on mobile
- Location management works on mobile
- Page loads in < 3 seconds on 3G

---

## Other Future Enhancements

### 1. Historical Rain Data Visualization
- Chart showing rain patterns over time per location
- Export rain history as CSV/JSON
- Statistics: total rainfall, frequency, etc.

### 2. Location Groups
- Organize locations into groups/folders
- Enable/disable entire groups
- Different notification settings per group

### 3. Advanced Alert Rules
- Custom thresholds per location
- Time-based rules (only alert during work hours)
- Intensity filters (only alert for heavy rain)
- Direction tracking (moving towards/away from location)

### 4. Push Notifications
- Native browser push notifications
- Service worker for offline support
- Background sync for alerts

### 5. Multi-User Support
- User authentication (OAuth)
- Per-user locations and settings
- Shared locations between users
- User roles and permissions

### 6. SMS Notifications
- Twilio integration
- SMS rate limiting
- Character count optimization

### 7. Voice Alerts
- Text-to-speech phone calls
- Twilio voice integration
- Custom voice messages

### 8. Radar Prediction
- 30-60 minute nowcasting
- Rain direction and speed tracking
- Estimated arrival time predictions

### 9. Historical Alert Log
- Searchable alert history
- Alert accuracy tracking
- Export alert history

### 10. API Access
- RESTful API for external integrations
- Webhooks for custom notifications
- API key management

---

## Notes

- Mobile mode is highest priority future feature
- Implementation should not break existing desktop UI
- All features should maintain Windows 98 aesthetic
- Performance and simplicity are key priorities
