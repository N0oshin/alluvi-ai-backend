# API Listing

All routes below are mounted under the `/api` prefix (`settings.API_V1_PREFIX`), except `/health` and the `/media` static mount, which sit at the root.

Every request/response model inherits `CamelModel`, so **all JSON field names are camelCase on the wire**. The names in this document are the wire names.

Auth is `Authorization: Bearer <accessToken>`. "Auth" column: **Yes** = requires a valid access token.

Error responses share one envelope from `app/core/errors.py` and are not repeated per endpoint.

---

## Meta

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/health` | No |

**Response** — `dict[str, str]`

| Field | Type |
| --- | --- |
| `status` | string (always `"ok"`) |

Also mounted: `GET /media/{path}` — static files (meal photos), `GET /docs`, `GET /openapi.json`.

---

## Auth — `/api/Auth`

### POST `/api/Auth/SignUp` → 201 · Auth: No

**Request** — `SignUpRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | 1–120 chars |
| `email` | string (email) |  |
| `password` | string | 8+ chars, must contain a number and a symbol |

**Response** — `SignUpResponse`

| Field | Type |
| --- | --- |
| `email` | string |
| `verificationRequired` | boolean (default `true`) |

No session is issued — the emailed 6-digit code must be verified first.

---

### POST `/api/Auth/login` → 200 · Auth: No

**Request** — `LoginRequest`

| Field | Type |
| --- | --- |
| `email` | string (email) |
| `password` | string |

**Response** — `TokenResponse`

| Field | Type | Notes |
| --- | --- | --- |
| `token` | string | access token |
| `refreshToken` | string |  |
| `expiresIn` | integer | seconds |
| `firstName` | string |  |
| `lastMobileDigit` | string | always `""` |
| `isNewUser` | boolean | default `false` |
| `emailVerified` | boolean | default `true` |

Returns 409 `EMAIL_NOT_VERIFIED` (and re-sends a code) if the address is unverified.

---

### POST `/api/Auth/verifyCode` → 200 · Auth: No

**Request** — `VerifyCodeRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `email` | string (email) |  |
| `code` | string | exactly 6 chars |

**Response** — `TokenResponse` (see above)

---

### POST `/api/Auth/resendCode` → 200 · Auth: No

**Request** — `ResendCodeRequest`

| Field | Type |
| --- | --- |
| `email` | string (email) |

**Response** — `MessageResponse`

| Field | Type |
| --- | --- |
| `message` | string |

Generic reply regardless of whether the address exists. 429 `OTP_COOLDOWN` if re-requested too soon.

---

### POST `/api/Auth/forgotPassword` → 200 · Auth: No

**Request** — `ForgotPasswordRequest`

| Field | Type |
| --- | --- |
| `email` | string (email) |

**Response** — `MessageResponse` (`message`: string). Emails a reset **link**, not a code.

---

### POST `/api/Auth/resetPassword` → 200 · Auth: No

**Request** — `ResetPasswordRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `token` | string | from the emailed link |
| `password` | string | same policy as sign-up |

**Response** — `MessageResponse` (`message`: string). Revokes every existing session.

---

### POST `/api/Auth/apple` → 200 · Auth: No
### POST `/api/Auth/google` → 200 · Auth: No

**Request** — `SocialAuthRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `identityToken` | string | provider identity JWT |
| `name` | string \ | null |

**Response** — `TokenResponse` (see above)

> Currently always returns 501 `SOCIAL_AUTH_NOT_CONFIGURED` — token verification is not implemented.

---

### POST `/api/Auth/refresh` → 200 · Auth: No

**Request** — `RefreshRequest`

| Field | Type |
| --- | --- |
| `refreshToken` | string |

**Response** — `TokenResponse` (see above). Rotates the token; replaying a consumed token revokes the whole family.

---

### POST `/api/Auth/logout` → 200 · Auth: No

**Request** — `LogoutRequest`

| Field | Type |
| --- | --- |
| `refreshToken` | string |

**Response** — `MessageResponse` (`message`: string). Idempotent.

---

### DELETE `/api/Auth/account` → 200 · Auth: **Yes**

**Request** — none

**Response** — `MessageResponse` (`message`: string). Hard-deletes the user, revokes all sessions, purges stored photos.

---

## Plan / Onboarding — `/api/userinfo`, `/api/profile/nutritionGoals`

### POST `/api/userinfo/plan` → 200 · Auth: **Yes**

**Request** — `PlanRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `gender` | string \ | null |
| `activityLevel` | string \ | null |
| `heightCm` | float | default 170, 50 < x < 280 |
| `weightKg` | float | default 65, 20 < x < 500 |
| `goal` | string \ | null |
| `desiredWeightKg` | float | default 58, 20 < x < 500 |
| `birthday` | date \ | null |

**Response** — `PlanOut`

| Field | Type |
| --- | --- |
| `dailyCalories` | integer |
| `proteinG` | integer |
| `carbsG` | integer |
| `fatsG` | integer |
| `weightDeltaKg` | float |
| `targetDate` | date \ |
| `planVersion` | integer |
| `isOverride` | boolean |
| `computedAt` | datetime |

---

### GET `/api/userinfo/plan` → 200 · Auth: **Yes**

**Request** — none. **Response** — `PlanOut` (see above). 404 `NO_PLAN` if onboarding never ran.

---

### GET `/api/profile/nutritionGoals` → 200 · Auth: **Yes**

**Request** — none

**Response** — `NutritionGoalsOut`

| Field | Type |
| --- | --- |
| `calories` | float |
| `protein` | float |
| `carbs` | float |
| `fats` | float |
| `isOverride` | boolean |

404 `NO_PLAN` if there is no active plan.

---

### PUT `/api/profile/nutritionGoals` → 200 · Auth: **Yes**

**Request** — `NutritionGoalsUpdate`

| Field | Type | Notes |
| --- | --- | --- |
| `calories` | float | 0 < x < 20000 |
| `protein` | float | 0 ≤ x < 2000 |
| `carbs` | float | 0 ≤ x < 2000 |
| `fats` | float | 0 ≤ x < 2000 |

**Response** — `NutritionGoalsOut` (see above), with `isOverride: true`.

---

## Food & Meals

### POST `/api/food/analyze` → 200 · Auth: **Yes**

**Request** — `multipart/form-data`

| Field | Type | Notes |
| --- | --- | --- |
| `image` | file | required; max 15 MB; re-encoded to JPEG |
| `container` | string | optional; `plate` \| `bowl` \| `cup` \| `glass` \| `packaged` \| `other`. Bounds the depth the photo cannot show. An unrecognised value is ignored, not rejected |

**Response** — `FoodAnalysisOut`

| Field | Type | Notes |
| --- | --- | --- |
| `analysisId` | UUID |  |
| `name` | string |  |
| `timeLabel` | string | `"HH:MM"` |
| `mealTypeLabel` | string | e.g. `"LUNCH"` |
| `caloriesPerServing` | integer |  |
| `proteinGramsPerServing` | integer |  |
| `carbsGramsPerServing` | integer |  |
| `fatGramsPerServing` | integer |  |
| `proteinProgress` | float | 0..1 share of daily target |
| `carbsProgress` | float | 0..1 |
| `fatProgress` | float | 0..1 |
| `healthScore` | integer |  |
| `healthScoreMax` | integer | default 10 |
| `estimatedPortionGrams` | integer \ | null | portion the estimate assumes |
| `portionConfidence` | string | `low` \| `medium` \| `high` |
| `scaleReference` | string \ | null | known-size object the portion was calibrated against, e.g. `"fork"`; `null` = none visible |
| `imageUrl` | string \ | null |
| `detectedItems` | array of `DetectedItemOut` |  |

`DetectedItemOut`: `label` (string), `cx` (float), `cy` (float).

---

### POST `/api/meals` → 201 · Auth: **Yes**

**Request** — `SaveMealRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `analysisId` | UUID |  |
| `quantity` | float | default 1.0, >0 and ≤99; 0.5 = half the portion |
| `mealType` | string \ | null |
| `caloriesOverride` | integer \ | null |
| `isFavorite` | boolean | default `false` |
| `eatenAt` | datetime \ | null |

**Response** — `MealOut`

| Field | Type |
| --- | --- |
| `id` | UUID |
| `title` | string |
| `imageUrl` | string (default `""`) |
| `calories` | integer |
| `proteinGrams` | integer |
| `carbsGrams` | integer |
| `fatGrams` | integer |
| `time` | string (`"HH:MM"`) |
| `mealType` | string |
| `healthScore` | integer |
| `isFavorite` | boolean |
| `eatenAt` | datetime |

---

### GET `/api/meals` → 200 · Auth: **Yes**

**Query parameters**

| Param | Type | Notes |
| --- | --- | --- |
| `day` | date \ | null |
| `limit` | integer | default 50, 1–200 |

**Response** — array of `MealOut` (see above)

---

### PATCH `/api/meals/{mealId}` → 200 · Auth: **Yes**

**Path** — `mealId` (UUID)

**Request** — `UpdateMealRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `quantity` | float \ | null | >0 and ≤99 |
| `calories` | integer \ | null |
| `isFavorite` | boolean \ | null |
| `mealType` | string \ | null |

**Response** — `MealOut` (see above)

---

### DELETE `/api/meals/{mealId}` → 200 · Auth: **Yes**

**Path** — `mealId` (UUID). **Request** — none.

**Response** — `MessageResponse` (`message`: string). Deletes the associated photo too.

---

### GET `/api/home/summary` → 200 · Auth: **Yes**

**Query parameters** — `day` (date \| null, defaults to today UTC)

**Response** — `DaySummaryOut`

| Field | Type | Notes |
| --- | --- | --- |
| `date` | date |  |
| `caloriesLeft` | integer | remaining, not consumed |
| `calorieGoal` | integer |  |
| `caloriesConsumed` | integer |  |
| `proteinLeft` | integer |  |
| `proteinGoal` | integer |  |
| `carbsLeft` | integer |  |
| `carbsGoal` | integer |  |
| `fatLeft` | integer |  |
| `fatGoal` | integer |  |
| `meals` | array of `MealOut` |  |

---

### GET `/api/home/week` → 200 · Auth: **Yes**

**Query parameters** — `anchor` (date \| null, defaults to today)

**Response** — array of `WeekDayOut` (7 items, Sunday→Saturday)

| Field | Type |
| --- | --- |
| `date` | date |
| `dayLabel` | string (single letter) |
| `dayNumber` | integer |
| `hasData` | boolean |
| `isToday` | boolean |

---

## Favorites

### GET `/api/favorites` → 200 · Auth: **Yes**

**Request** — none

**Response** — array of `FavoriteOut`

| Field | Type |
| --- | --- |
| `id` | UUID |
| `title` | string |
| `kcal` | integer |
| `mealType` | string |
| `tag` | string |
| `imageUrl` | string \ |
| `isFavorite` | boolean |

---

### POST `/api/favorites/{mealId}/toggle` → 200 · Auth: **Yes**

**Path** — `mealId` (UUID). **Request** — none.

**Response** — `FavoriteOut` (see above)

---

## Analytics

### GET `/api/analytics` → 200 · Auth: **Yes**

**Query parameters**

| Param | Type | Notes |
| --- | --- | --- |
| `range` | string | default `90d`; one of `90d` \ |

Also reads the `Accept-Language` header for the localised BMI category label.

**Response** — `AnalyticsOut`

| Field | Type |
| --- | --- |
| `goalProgress` | `GoalProgressOut` |
| `currentBmi` | `BmiOut` \ |
| `streak` | `StreakOut` |
| `caloriesThisWeek` | array of `DayCaloriesOut` |

`GoalProgressOut`: `currentWeightKg` (float \| null), `startingWeightKg` (float \| null), `goalWeightKg` (float \| null), `deltaKg` (float), `series` (array of `WeightPointOut`).
`WeightPointOut`: `label` (string), `kg` (float), `date` (date).
`BmiOut`: `value` (float), `category` (string, localised), `categoryKey` (string).
`StreakOut`: `days` (integer).
`DayCaloriesOut`: `dayLabel` (string), `calories` (float), `isActive` (boolean).

---

## Profile — `/api/profile`

### GET `/api/profile` → 200 · Auth: **Yes**

**Request** — none

**Response** — `ProfileOut`

| Field | Type |
| --- | --- |
| `displayName` | string |
| `username` | string |
| `isPremium` | boolean |
| `caloriesLeft` | integer |
| `calorieGoal` | integer |
| `streakDays` | integer |
| `appleHealthConnected` | boolean |
| `lastSyncedAt` | datetime \ |

---

### GET `/api/profile/summary` → 200 · Auth: **Yes**

**Request** — none

**Response** — `ProfileSummaryOut`

| Field | Type |
| --- | --- |
| `displayName` | string |
| `goal` | string \ |
| `goalLabel` | string |
| `currentWeightKg` | float \ |
| `goalWeightKg` | float \ |
| `dailyCalories` | integer \ |

---

### GET `/api/profile/personalDetails` → 200 · Auth: **Yes**

**Request** — none

**Response** — `PersonalDetailsOut`

| Field | Type |
| --- | --- |
| `name` | string |
| `email` | string |
| `gender` | string |
| `heightCm` | float |
| `birthday` | date \ |
| `emailVerificationRequired` | boolean (default `false`) |

---

### PUT `/api/profile/personalDetails` → 200 · Auth: **Yes**

**Request** — `PersonalDetailsUpdate`

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string \ | null |
| `email` | string (email) \ | null |
| `gender` | string \ | null |
| `heightCm` | float \ | null |
| `birthday` | date \ | null |

**Response** — `PersonalDetailsOut` (see above). 409 `EMAIL_TAKEN` on a clash.

---

### GET `/api/profile/weight` → 200 · Auth: **Yes**

**Request** — none

**Response** — `CurrentWeightOut`

| Field | Type | Notes |
| --- | --- | --- |
| `startingWeightKg` | float |  |
| `currentWeightKg` | float |  |
| `goalWeightKg` | float |  |
| `estimatedGoalDate` | string | pre-formatted, e.g. `"Est. Sep 14"` |
| `targetDate` | date \ | null |

---

### PUT `/api/profile/weight` → 200 · Auth: **Yes**

**Request** — `WeightUpdateRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `currentWeightKg` | float \ | null |
| `goalWeightKg` | float \ | null |

**Response** — `CurrentWeightOut` (see above). Triggers a plan recalculation.

---

### GET `/api/profile/weightHistory` → 200 · Auth: **Yes**

**Query parameters** — `limit` (integer, default 365, 1–2000)

**Response** — array of `WeightEntryOut`, oldest first

| Field | Type |
| --- | --- |
| `date` | date |
| `kg` | float |
| `source` | string |

---

### POST `/api/profile/healthSync` → 200 · Auth: **Yes**

**Request** — `HealthSyncRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `samples` | array of `HealthSample` | max 1000 |

`HealthSample`: `externalId` (string, HealthKit UUID — the dedupe key), `kg` (float), `recordedOn` (date).

**Response** — `HealthSyncOut`

| Field | Type |
| --- | --- |
| `imported` | integer |
| `skipped` | integer |
| `lastSyncedAt` | datetime |

---

### GET `/api/profile/notificationSettings` → 200 · Auth: **Yes**

**Request** — none

**Response** — `NotificationSettingsOut`

| Field | Type |
| --- | --- |
| `mealReminders` | boolean |
| `streakReminder` | boolean |
| `weeklyReport` | boolean |
| `trialBillingAlerts` | boolean |
| `tipsAndArticles` | boolean |
| `quietStart` | string (`"HH:mm"`) |
| `quietEnd` | string (`"HH:mm"`) |

---

### PUT `/api/profile/notificationSettings` → 200 · Auth: **Yes**

**Request** — `NotificationSettingsUpdate` — same fields as above, all nullable/optional; `quietStart` / `quietEnd` must match `^\d{2}:\d{2}$`. Omitted (null) fields are left unchanged.

**Response** — `NotificationSettingsOut` (see above)

---

### GET `/api/profile/achievements` → 200 · Auth: **Yes**

**Request** — none

**Response** — `AchievementsOut`

| Field | Type |
| --- | --- |
| `unlocked` | integer |
| `total` | integer |
| `items` | array of `AchievementOut` |

`AchievementOut`: `id` (UUID), `key` (string), `label` (string), `iconKey` (string), `unlocked` (boolean).

---

### GET `/api/profile/ringColors` → 200 · Auth: **Yes**

**Request** — none

**Response** — array of `RingColorOut` (5 items: Calories, Protein, Carbs, Fats, Health score)

| Field | Type | Notes |
| --- | --- | --- |
| `title` | string |  |
| `description` | string |  |
| `value` | float | 0..1 |
| `colorToken` | string | theme token, not a hex |

---

### GET `/api/profile/inviteInfo` → 200 · Auth: **Yes**

**Request** — none

**Response** — `InviteInfoOut`

| Field | Type |
| --- | --- |
| `inviteCode` | string |
| `rewardTitle` | string |
| `rewardSubtitle` | string |
| `steps` | array of string |

Static copy — no referral reward is tracked or granted.

---

### POST `/api/profile/feedback` → 200 · Auth: **Yes**

**Request** — `FeedbackRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `message` | string | 1–5000 chars |

**Response** — `MessageResponse` (`message`: string)

---

## Legal — `/api/legal`

### GET `/api/legal/terms` → 200 · Auth: No
### GET `/api/legal/privacy` → 200 · Auth: No

**Request** — none. Reads `Accept-Language` to pick the language, falling back to `en`.

**Response** — `LegalDocumentOut`

| Field | Type |
| --- | --- |
| `lastUpdated` | string (e.g. `"March 04, 2026"`) |
| `intro` | string |
| `sections` | array of `LegalSectionOut` |
| `footerNote` | string |

`LegalSectionOut`: `title` (string), `body` (string).

---

## Notifications — `/api/UserNotification`

Both endpoints return a **bare JSON boolean**, not an envelope — the shipped client only records success on a literal `true` body.

### POST `/api/UserNotification/addAnonymousToken` → 200 · Auth: No

**Request** — `DeviceTokenRequest`

| Field | Type | Notes |
| --- | --- | --- |
| `token` | string | 1–512 chars; APNs or FCM |

**Response** — boolean (`true`)

---

### POST `/api/UserNotification/addUserToken` → 200 · Auth: **Yes**

**Request** — `DeviceTokenRequest` (same as above)

**Response** — boolean (`true`). Claims a token previously registered anonymously.
