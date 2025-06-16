## 3. Enthusiast Purchase, Slice, Print Workflow (Cloud Run Functions Prototype)

**Goal**: Enable [[Enthusiast]]s to purchase print rights for models, prepare models for printing by generating printer-specific G-code, and print models, paying PRINT/HBAR for slicing and printing, tracking print counts, enforcing print limits, and handling failed prints to preserve print rights. The workflow is split into modular Purchase, Prepare, and Print processes, using [[Cloud Run]] Functions for APIs, integrating with the OrcaSlicer-based slicing service and [[Printing Service]]s via [[Pub/Sub Queue Classes]], supporting non-exclusive, pay-per-print licensing.

**Estimated Effort**: 2 weeks
**Dependencies**: [[NFT Logic]] (Checklist 1), [[Publisher Workflow]] (Checklist 2)

**Tasks**:
### 3.1 Purchase Workflow
**Objective**: Enable Enthusiasts to purchase model print rights with an upfront fee, ensuring secure payment and licensing enforcement.

**Tasks**:
- [x] Define API schema for Purchase (e.g., `{ user_id, model_id, license_type, storage_currency }`).
- [x] Create CURL command for testing (`curl -X POST /api/purchase-model`).
- [x] Implement `/api/purchase-model` endpoint to:
  - Validate `model_id` and `license_type` in `PublishedModels` and `ModelLicenses`.
  - Verify wallet balance and transfer `price` (e.g., 10 HBAR) to Designer’s `token_treasury_account_id` via `TokenTransferTransaction`.
  - Insert purchase into `purchases` table (`purchase_id`, `user_id`, `model_id`, `nft_id`, `license_type`, `price`, `currency`, `prints_allowed`, `prints_used`).
  - Log `model_purchase` to [[HCS]] with `purchase_id`, `price`, `prints_allowed`.
  - Return `{ status, purchase_id, model_id, license_type, prints_allowed, prints_used }`.
- [x] Implement `purchaseModel` function to process payment via Custodial Wallet and HTS.
- [x] Test Purchase Workflow on local [[Hedera]] instance using CURL.
- [x] Verify [[HCS]] logs and database updates via API calls.

**Specification**:
- **API**: `POST /api/purchase-model`
  - **Request**: `{ "user_id": "string", "model_id": "string", "license_type": "string", "storage_currency": "string" }`
  - **Response**: `{ "status": "string", "purchase_id": "string", "model_id": "string", "license_type": "string", "prints_allowed": "integer", "prints_used": "integer" }`
  - **Example CURL**:
    ```bash
    curl -X POST https://marketplace-api.run.app/api/purchase-model \
         -H "Authorization: Bearer <JWT>" \
         -H "Content-Type: application/json" \
         -d '{"user_id":"Bob","model_id":"123","license_type":"pay-per-print","storage_currency":"hbar"}'
  

### 3.2 Prepare Workflow
**Objective**: Prepare purchased models for printing by generating G-code using slicer profiles, with scale-to-zero on Cloud Run (~38–60s worst-case latency), supporting immediate or queued printing.

**Tasks**:
- [x] Define API schemas for Prepare and status check (e.g., `{ user_id, purchase_id, print_profile_id, print_immediately }`).
- [x] Create CURL command for testing (`curl -X POST /api/prepare`).
- [x] Implement `/api/prepare` endpoint to:
  - [x] Validate `purchase_id` (`prints_used < prints_allowed`) and `print_profile_id` in `purchases` and `print_profiles`.
  - [x] Fetch print profile (`machine_settings`, `process_settings`, `filament`) from `print_profiles`.
  - [x] Verify GOOGLE_CLOUD_PROJECT in .env aligns with the target deployment environment (e.g., lmnt-dev for dev Pub/Sub).
  - [x] Confirm dev-slicing-jobs-subscription exists in the target GCP project for the slicing-jobs topic.
  - [x] Publish slicing job to `slicing-jobs` topic with `purchase_id`, `print_profile_id`, `stl_gcs_uri`, `stl_dek`, `stl_iv`.
  - Slicing service pulls encrypted STL from GCS, decrypts with DEK from Custodial Wallet.
  - Generate G-code via `/slice` endpoint (Node.js, OrcaSlicer).
  - Re-encrypt G-code with new DEK, store in GCS (`gs://3d-marketplace-gcode/...`), set 24-hour TTL.
  - Store encrypted DEK in `purchases.gcode_dek`, encrypted with printer-specific KEK from `printers`.
  - Charge optional slicing/storage fee (e.g., 0.1 HBAR), log to HCS.
  - Update `purchases` (`gcode_dek`, `slicing_fee`, `gcode_file_url`, `gcode_ttl_expiry`, `print_immediately`).
  - If `print_immediately: true`, trigger `/api/print`.
  - Return `{ status, purchase_id, gcode_file_url, print_profile_id, slicing_fee, ttl_hours }`.
- [ ] Implement `/api/prepare-status` endpoint to return job status (`pending`, `in_progress`, `completed`, `printing`).
- [x] Implement `prepareModel` function to manage slicing via Pub/Sub and OrcaSlicer.
- [x] Test Prepare Workflow on local [[Hedera]] instance using CURL, including ~60s latency.
- [x] Verify [[HCS]] logs, GCS storage, and database updates.

**Specification**:
- **API**:
  - `POST /api/prepare`
    - **Request**: `{ "user_id": "string", "purchase_id": "string", "print_profile_id": "string", "storage_currency": "string", "print_immediately": "boolean" }`
    - **Response**: `{ "status": "string", "purchase_id": "string", "gcode_file_url": "string", "print_profile_id": "string", "slicing_fee": "number", "ttl_hours": "integer" }`
    - **Example CURL**:
      ```bash
      curl -X POST https://marketplace-api.run.app/api/prepare \
           -H "Authorization: Bearer <JWT>" \
           -H "Content-Type: application/json" \
           -d '{"user_id":"Bob","purchase_id":"purch_789","print_profile_id":"profile_456","storage_currency":"hbar","print_immediately":true}'
      - **Key Details**:
        - API: `POST /api/prepare`, request/response JSON as per May 29, 2025, 6:12 AM PDT.
        - Database: Query `PublishedModels`, `ModelNFTSettings`, `purchases`, `print_profiles`; update `purchases`.
        - Hedera: HCS log (`model_prepare`, `slicing_fee`), `TokenTransferTransaction` for fee.
        - Pub/Sub: Publish to `slicing-jobs`, subscribe to `slicing-results`.
        - Slicing Service: OrcaSlicer 3GB Docker container, `/slice` endpoint, deployed on Cloud Run (4GB RAM, 2 vCPUs).
        - Encryption: STL decryption (AES-256-CBC, `model_file_iv`), G-code re-encryption, DEK in `purchases.gcode_dek`.
        - Security: JWT auth, signed GCS URLs, in-memory processing.
        - Testing: Local mock Hedera/Pub/Sub, testnet with HBAR, edge cases (invalid profiles, slicing failures).


  Potential SQL Schema Changes:
    ALTER TABLE purchases
    ADD gcode_dek VARCHAR(256),
    ADD slicing_fee NUMERIC DEFAULT 0,
    ADD gcode_file_url VARCHAR(256),
    ADD gcode_ttl_expiry TIMESTAMPTZ,
    ADD print_immediately BOOLEAN DEFAULT FALSE,
    ADD print_status VARCHAR(20),
    ADD print_error TEXT;


### 3.3 Print Workflow
**Objective**: Authorize and stream encrypted G-code to the Enthusiast’s printer for in-memory decryption and printing, triggered immediately post-slicing or manually from queued jobs, with status feedback and minimal unencrypted G-code exposure.

**Tasks**:
- [x] Define API schemas for Print, job retrieval, printer registration, and status reporting.
- [x] Create CURL commands for testing (`curl -X POST /api/print`, `curl -X GET /api/get-print-job`, `curl -X POST /api/register-printer`, `curl -X POST /api/report-print-status`).
- [x] Implement `/api/print` endpoint to:
  - Validate `purchase_id` (`prints_used < prints_allowed`) and `gcode_file_url` in `purchases`.
  - Verify G-code TTL (`gcode_ttl_expiry > NOW()`).
  - Publish print job to `print-jobs` topic with `purchase_id`, `gcode_file_url`.
  - Return `{ status, purchase_id, print_job_id }`.
- [x] Implement `/api/get-print-job` endpoint to:
  - Authenticate printer with long-lived JWT.
  - Return `gcode_file_url` and encrypted `gcode_dek` from `purchases` for `purchase_id`.
  - Example Response: `{ "gcode_file_url": "gs://...", "gcode_dek": "<encrypted_dek>" }`.
- [x] Implement `/api/register-printer` endpoint to:
  - [x] Locally generate a unique Printer-Specific Encryption Key (PSEK).
  - [x] Send the PSEK to the Custodial Wallet Service (CWS) to be envelope-encrypted using the Master Printer KEK.
  - [x] Store the CWS-returned encrypted PSEK in the `printers` table (currently in the `kek_id` column).
  - [x] Return comprehensive printer details, including `printer_id` and the `encrypted_psek` (stored as `kek_id`).
  - [x] Generate and return a long-lived JWT (e.g., 30-day expiry) specifically for the printer to authenticate subsequent machine-to-machine requests (e.g., to `/api/get-print-job`). (Future Step)
- [ ] Implement `/api/report-print-status` endpoint to:
  - Process success/failure reports from `hedera_slicer.py`.
  - If `status = "success"`, increment `prints_used` in `purchases`, log `model_print` to [[HCS]].
  - If `status = "failure"`, don’t increment `prints_used`, log `print_failure` to [[HCS]].
  - Update `purchases` (`print_status`, `print_error`).
  - Return `{ status, purchase_id, print_job_id, print_outcome }`.
- [x] Refactor `hedera_slicer.py` to:
  - Subscribe to `print-jobs` topic for print tasks.
  - Authenticate with long-lived JWT to call `/api/get-print-job`.
  - Fetch encrypted G-code from GCS, decrypt in-memory using DEK (Fernet).
  - Stream G-code to Klipper via `STREAM_GCODE_LINE`, avoiding disk storage.
  - Report status to `/api/report-print-status`.
- [x] Enhance refund logic in `hedera_slicer.py` using `print_stats.py`.
- [x] Create `printers` table in LMNT Marketplace database:
  ```sql
  CREATE TABLE printers (
    printer_id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    kek_encrypted VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
  );
        - API: `POST /api/report-print-status`
          - Request: `{ "user_id": "string", "purchase_id": "string", "print_job_id": "string", "status": "string", "error": "string" }`
          - Response: `{ "status": "string", "purchase_id": "string", "print_job_id": "string", "print_outcome": "string" }`
        - Database: Update `purchases` (`print_status`, `print_error`).
        - Hedera: HCS log (`print_failure` for failures).
        - Security: JWT auth, validate `print_job_id`.
        - Testing: Local mock printer, testnet with failure simulation, edge cases (invalid `print_job_id`).


#### Checklist 3.3.1 Printer Web Service Integration

**Objective**: Develop a web service component for the Moonraker plugin that provides a user-friendly interface for printer registration, authentication, and marketplace integration, with the ability to be embedded as a tab in printer interfaces like Mainsail or Fluidd.

**Tasks**:
- [ ] Design the web service architecture:
  - [ ] Define API endpoints for integration with Moonraker
  - [ ] Plan secure storage for JWT and credentials
  - [ ] Design UI components and user flows
  - [ ] Determine integration points with Mainsail/Fluidd
- [ ] Implement core authentication functionality:
  - [ ] Create login interface using email/password authentication
  - [ ] Develop secure session management
  - [ ] Implement printer registration workflow
  - [ ] Add JWT storage and management
- [ ] Develop printer management features:
  - [ ] Display printer registration status
  - [ ] Show marketplace connection status
  - [ ] Implement token renewal functionality
  - [ ] Add printer configuration options
- [ ] Implement print queue management:
  - [ ] Display available purchased models
  - [ ] Create interface for queue management
  - [ ] Add print history and status tracking
  - [ ] Implement print job controls
- [ ] Integrate with Mainsail/Fluidd:
  - [ ] Develop as embeddable tab component
  - [ ] Ensure responsive design for various devices
  - [ ] Implement consistent styling with host UI
  - [ ] Add configuration options for UI integration
- [ ] Security enhancements:
  - [ ] Implement HTTPS for local web interface
  - [ ] Add proper error handling and validation
  - [ ] Create secure storage mechanisms
  - [ ] Develop audit logging for security events
- [ ] Testing and documentation:
  - [ ] Create user documentation
  - [ ] Develop installation guide
  - [ ] Write developer documentation
  - [ ] Implement automated tests
