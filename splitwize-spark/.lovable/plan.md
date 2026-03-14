

# GroupPay Prototype — Full Port

## Overview
Port the complete GroupPay bill-splitting prototype into the Lovable project as a single-page app with all flows, analytics tracking, and survey modal intact.

## What will be built

### 1. Main Bill Splitting Flow
A step-by-step wizard with the dark glassmorphism design:
- **Start screen** — welcome with feature highlights
- **OCR choice** — scan receipt or enter manually
- **OCR scan** — simulated camera scan with spinner animation
- **OCR result** — displays mock scanned receipt items and totals
- **Manual entry** — text input for bill total
- **Participants** — add/remove Telegram handles
- **Split type** — choose even split or custom amounts
- **Custom split** — per-person amount entry
- **Overview** — review split before confirming
- **QR codes** — show simulated PayNow QR per participant with payment status tracking
- **Payment verification** — simulated screenshot upload & verification flow
- **Payment confirmed** — success screen with details
- **Reminders** — nudge unpaid participants

### 2. Analytics System (localStorage)
- Session tracking with events, clicks, navigation, and task timing
- Export analytics to CSV
- Analytics dashboard panel (fixed top-right button)

### 3. User Validation Survey
- Modal with 8 questions (SUS scale, feature ratings, open text)
- Required field validation
- Multiple close methods (X button, ESC, click outside, cancel)
- Survey responses saved to localStorage and exportable as CSV

### 4. Design
- Dark gradient background (slate-900 → blue-900)
- Glassmorphism cards with backdrop blur
- DM Sans + JetBrains Mono fonts
- Slide-in animations
- Blue gradient primary buttons with hover effects
- Mobile-first layout (max-w-md centered)

