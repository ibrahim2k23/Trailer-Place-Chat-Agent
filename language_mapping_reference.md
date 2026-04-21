# Trailer Language Mapping — Agent Reference
Source: `Trailer_Language_Mapping_agent_ready_v2 4-15-26.xlsx`
Captured: 2026-04-17

---

## 1. Intent Router

| intent_id | customer_intent | example_phrases | route_to | required_slots | handoff_rule | priority |
|---|---|---|---|---|---|---|
| trailer_selection | Customer wants help finding the right trailer | I need a trailer; what trailer do I need; what do you recommend | Language_Map → Category_Taxonomy → Question_Flows | category_or_use_case | handoff only if customer requests person or inventory-specific quote | 10 |
| inventory_lookup | Customer wants to see available trailers | what do you have in stock; show me inventory; do you have any available | inventory_search | category_id; optional size/weight filters | handoff if inventory system not available or customer wants live availability | 10 |
| pricing_quote | Customer wants pricing or a quote | how much is it; what is the price; can I get a quote | quote_or_price_flow | category_id or stock_number | handoff to sales if specific quote requested | 9 |
| financing | Customer asks about financing | do you finance; monthly payment; trailer financing | finance_flow | approx purchase type; budget optional | handoff to finance or sales | 9 |
| trade_in | Customer asks about trading in a trailer | can I trade mine in; trade in value; I have a trailer to trade | trade_in_flow | year_make_model; condition optional | handoff to sales | 9 |
| service | Customer needs repair or service | do you fix trailers; service department; I need repair | service_flow | issue_type; trailer_type optional | handoff to service | 9 |
| parts | Customer needs trailer parts or accessories | do you have trailer parts; I need a jack; trailer lights | parts_flow | part_needed; trailer_info optional | handoff to parts if exact part lookup needed | 8 |
| store_info | Customer asks about location, hours, or contact | where are you located; what are your hours; phone number | store_info_response | none | none | 8 |
| human_handoff | Customer wants a person immediately | can I talk to someone; sales rep; call me | handoff | name optional; phone optional | immediate handoff | 10 |

---

## 2. Category Taxonomy

### Equipment Trailer
- **entity_type:** core_category
- **description:** Open trailer for hauling equipment and machinery
- **common_uses:** Skid steers, mini excavators, tractors, compact equipment
- **required_slots:** haul_item; haul_weight_lbs; haul_length_ft; tow_vehicle
- **next_questions:** What are you hauling?; About how much does it weigh?; About how long is it?; What are you towing with?
- **inventory_tags:** equipment; open; ramps; machine_hauling
- **customer_friendly_prompt:** It sounds like you may be looking for an equipment trailer.
- **notes:** Use when customer talks about machinery or loading height.

### Car Hauler
- **entity_type:** core_category
- **description:** Open-deck trailer typically used for transporting vehicles
- **common_uses:** Cars, Jeeps, UTVs, small vehicles
- **required_slots:** haul_item; haul_weight_lbs; haul_length_ft; tow_vehicle
- **next_questions:** What vehicle are you hauling?; About how much does it weigh?; What are you towing with?
- **inventory_tags:** car_hauler; open; vehicle_transport
- **customer_friendly_prompt:** It sounds like you may be looking for a car hauler.
- **notes:** Use when customer is transporting road vehicles on an open deck.

### Utility Trailer
- **entity_type:** core_category
- **description:** General-purpose open trailer for smaller equipment and commercial yard use
- **common_uses:** Lawn mowers, ATVs, landscaping tools
- **required_slots:** haul_item; haul_weight_lbs; haul_length_ft; tow_vehicle
- **next_questions:** What are you hauling?; About how much does it weigh?; Do you need tall sides or tool storage?
- **inventory_tags:** utility; open; lawn; landscaping
- **customer_friendly_prompt:** It sounds like a utility trailer may fit.
- **notes:** Use for lighter-duty open hauling and landscaping jobs.

### Dump Trailer
- **entity_type:** core_category
- **description:** Trailer with hydraulic dump function for loose materials
- **common_uses:** Gravel, dirt, debris, demolition material
- **required_slots:** haul_material; haul_weight_lbs; tow_vehicle
- **next_questions:** What material are you hauling?; About how much weight?; What are you towing with?
- **inventory_tags:** dump; hydraulic; material_hauling
- **customer_friendly_prompt:** It sounds like you may be looking for a dump trailer.
- **notes:** Ask mechanism preference only after use case is clear.

### Tilt Trailer
- **entity_type:** core_category
- **description:** Trailer that tilts for easier loading without separate ramps
- **common_uses:** Vehicles, equipment, low-clearance loading
- **required_slots:** haul_item; haul_weight_lbs; haul_length_ft; tow_vehicle
- **next_questions:** What are you loading?; About how much does it weigh?; Do you want full tilt or stationary deck with tilt section?
- **inventory_tags:** tilt; equipment; vehicle_transport
- **customer_friendly_prompt:** It sounds like you may be looking for a tilt trailer.
- **notes:** Useful when customer cares about loading style.

### Enclosed Trailer
- **entity_type:** core_category
- **description:** Covered trailer for protected cargo or customized interiors
- **common_uses:** Cargo, tools, vending, office, camping, covered vehicle transport
- **required_slots:** use_case; cargo_size; tow_vehicle
- **next_questions:** What are you using it for?; What size do you need?; What are you towing with?
- **inventory_tags:** enclosed; cargo; covered
- **customer_friendly_prompt:** It sounds like you may be looking for an enclosed trailer.
- **notes:** Use for cargo or specialty enclosed applications.

### Livestock Trailer
- **entity_type:** core_category
- **description:** Trailer built for hauling animals
- **common_uses:** Cattle, horses, goats, hogs, mixed livestock
- **required_slots:** animal_type; animal_count; trailer_length_ft; tow_vehicle
- **next_questions:** What type of animals are you hauling?; How many?; What are you towing with?
- **inventory_tags:** livestock; stock; horse; cattle
- **customer_friendly_prompt:** It sounds like you may be looking for a livestock trailer.
- **notes:** Use horse/cattle subtype if clear.

### Roll-Off Package
- **entity_type:** core_category
- **description:** Trailer package used with roll-off dumpster bins
- **common_uses:** Dumpster service, bin drop-off, waste hauling
- **required_slots:** bin_size; trailer_capacity; tow_vehicle
- **next_questions:** Are you looking for the trailer package, the bins, or both?; What bin size do you need?
- **inventory_tags:** roll_off; dumpster; bins
- **customer_friendly_prompt:** It sounds like you may be looking for a roll-off package.
- **notes:** Separate trailer-package requests from bin-only requests.

### Tank Trailer
- **entity_type:** core_category
- **description:** Trailer for fuel or liquid storage/transport applications
- **common_uses:** Diesel, gasoline, jobsite fuel support
- **required_slots:** fuel_type; tank_capacity; tow_vehicle
- **next_questions:** Will this be for diesel or gasoline?; What capacity do you need?; What are you towing with?
- **inventory_tags:** tank; fuel; diesel; gasoline
- **customer_friendly_prompt:** It sounds like you may be looking for a tank trailer.
- **notes:** Confirm fuel type and compliance requirements.

### Flatbed / Hotshot Trailer
- **entity_type:** core_category
- **description:** Flatbed trailer for heavy materials or larger equipment, often gooseneck-style
- **common_uses:** Hotshot hauling, pallets, materials, large equipment
- **required_slots:** haul_item; haul_weight_lbs; haul_length_ft; tow_vehicle
- **next_questions:** What are you hauling?; About how much does it weigh?; Do you need step deck or standard flatbed?
- **inventory_tags:** flatbed; hotshot; gooseneck; materials
- **customer_friendly_prompt:** It sounds like you may be looking for a flatbed or hotshot trailer.
- **notes:** Ask CDL / axle questions only after use case is known.

### Race Trailer
- **entity_type:** specialty_use (parent: enclosed)
- **description:** Specialty enclosed trailer configured for race support and vehicle transport
- **common_uses:** Race cars, tools, pits, covered vehicle hauling
- **required_slots:** vehicle_type; trailer_length_ft; interior_features; tow_vehicle
- **next_questions:** What vehicle are you hauling?; Do you need cabinets, work area, or living features?
- **inventory_tags:** race; enclosed; car_hauler
- **customer_friendly_prompt:** It sounds like you may be looking for a race trailer.
- **notes:** Usually a specialty enclosed trailer.

### Fiber Splicing / Jobsite Trailer
- **entity_type:** specialty_use (parent: enclosed)
- **description:** Specialty enclosed trailer for fiber optic splicing or telecom jobsite support
- **common_uses:** Fiber splicing, remote office, cooldown on jobsites
- **required_slots:** use_case; crew_size; climate_control; tow_vehicle
- **next_questions:** Will this be for fiber splicing, a remote office, or cooldown space?; Do you need AC and windows?
- **inventory_tags:** fiber; telecom; jobsite; enclosed
- **customer_friendly_prompt:** It sounds like you may be looking for a specialty jobsite trailer.
- **notes:** Can overlap with office/cooldown language.

### Welding Trailer
- **entity_type:** specialty_use (parent: utility)
- **description:** Specialty trailer set up to carry welding equipment and torches
- **common_uses:** Welding rig, torch setup, field repair
- **required_slots:** equipment_list; weight_lbs; tow_vehicle
- **next_questions:** What welding equipment are you carrying?; About how much does it weigh?; What are you towing with?
- **inventory_tags:** welding; utility; jobsite
- **customer_friendly_prompt:** It sounds like you may be looking for a welding trailer.
- **notes:** Usually a specialty utility/commercial configuration.

### Aluminum Build Preference *(modifier, not standalone)*
- **entity_type:** material_or_build
- **description:** Material preference for lighter weight and corrosion resistance
- **common_uses:** Lightweight builds, rust resistance, Aluma models
- **required_slots:** base_category; tow_vehicle; payload_need
- **next_questions:** Are you looking for a specific aluminum model or just a lighter trailer?; What are you hauling?
- **inventory_tags:** aluminum; lightweight; corrosion_resistant
- **customer_friendly_prompt:** It sounds like aluminum may be the build style you want.
- **notes:** Modifier, not a standalone trailer family. Always resolve base category first.

### Offroad / Camping Package
- **entity_type:** specialty_use (parent: enclosed)
- **description:** Specialty offroad enclosed/camping trailer
- **common_uses:** Camping, overlanding, offroad support
- **required_slots:** use_case; sleeping_need; tow_vehicle
- **next_questions:** Will you be camping or hauling gear?; Do you need offroad tires or an interior setup?
- **inventory_tags:** offroad; camping; enclosed
- **customer_friendly_prompt:** It sounds like an offroad camping trailer may fit.
- **notes:** Usually a specialty enclosed trailer.

---

## 3. Language Map (54 phrases)

| phrase_id | customer_phrase | mapped_category_id | mapped_specialty_id | mapped_modifier_id | mapped_subcategory | confidence | disambiguation_question | inventory_tags |
|---|---|---|---|---|---|---|---|---|
| LP-001 | Looking for Lowboy Trailer | equipment | — | — | low_profile | 0.90 | Are you hauling equipment and looking for a lower deck height for easier loading? | equipment |
| LP-002 | Looking for a Low Profile Trailer | equipment | — | — | low_profile | 0.90 | Are you hauling equipment and looking for a lower deck height for easier loading? | equipment |
| LP-003 | Looking for a DeckOver Bumper Pull | equipment | — | — | deckover_bumper_pull | 0.90 | What are you hauling, and about how much does it weigh? | equipment |
| LP-004 | Looking for a skid steer trailer | equipment | — | — | — | 0.90 | What are you hauling, and about how much does it weigh? | equipment |
| LP-005 | Looking to haul a mini ex or mini excavator | equipment | — | — | — | 0.90 | What are you hauling, and about how much does it weigh? | equipment |
| LP-006 | Looking for trailer without sides | car_hauler | — | — | — | 0.90 | What vehicle are you hauling, and about how much does it weigh? | car_hauler |
| LP-007 | Looking for toy hauler | car_hauler | — | — | — | 0.55 | Will you be hauling a vehicle on an open deck, or are you looking for a camper-style toy hauler? | car_hauler |
| LP-008 | Looking for a Landscape Trailer | utility | — | — | landscape | 0.90 | What are you hauling, and do you need sides or tool storage? | utility |
| LP-009 | Looking for Lawnmower/ATV trailer | utility | — | — | — | 0.90 | What are you hauling, and do you need sides or tool storage? | utility |
| LP-010 | Looking for a scissor lift, hoist, dump trailer | dump | — | — | scissor_hoist | 0.90 | What material are you hauling, and about how much weight? | dump |
| LP-011 | Looking for a telescopic or front lift dump trailer | dump | — | — | telescopic | 0.90 | What material are you hauling, and about how much weight? | dump |
| LP-012 | Looking for a full tilt bed trailer | tilt | — | — | full_tilt | 0.90 | What are you loading, and do you want full tilt or a stationary front deck? | tilt |
| LP-013 | Looking for a power tilt trailer | tilt | — | — | power_tilt | 0.90 | What are you loading, and do you want full tilt or a stationary front deck? | tilt |
| LP-014 | Looking for gravity/hydraulically dampened tilt | tilt | — | — | gravity_tilt | 0.90 | What are you loading, and do you want full tilt or a stationary front deck? | tilt |
| LP-015 | Looking for a gravity tilt | tilt | — | — | gravity_tilt | 0.90 | What are you loading, and do you want full tilt or a stationary front deck? | tilt |
| LP-016 | Looking for a Box Trailer | enclosed | — | — | cargo | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-017 | Looking for a cargo trailer | enclosed | — | — | cargo | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-018 | Looking for a V-Nose Trailer | enclosed | — | — | v_nose | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-019 | Looking for a concession/race trailer | enclosed | — | — | specialty | 0.90 | Will you be using it for vending, racing, or another specialty setup? | enclosed |
| LP-020 | Looking for an office trailer | enclosed | — | — | office | 0.90 | Will this be a general office-style enclosed trailer or something for fiber / telecom work? | enclosed |
| LP-021 | Looking for a cooldown trailer | enclosed | — | — | specialty_climate_control | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-022 | Looking for a job site trailer | enclosed | — | — | specialty_climate_control | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-023 | Looking for a command center | enclosed | — | — | specialty_climate_control | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-024 | Looking for a camping trailer | enclosed | — | — | specialty_climate_control | 0.90 | What are you using it for, and what size do you need? | enclosed |
| LP-025 | Looking for a jeep trailer | enclosed | offroad | — | — | 0.85 | Will you be using it mainly for camping / overlanding, or for covered gear hauling? | enclosed; offroad |
| LP-026 | Looking for an offroad trailer | enclosed | offroad | — | — | 0.85 | Will you be using it mainly for camping / overlanding, or for covered gear hauling? | enclosed; offroad |
| LP-027 | Looking for cattle trailer | livestock | — | — | cattle | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-028 | Looking for a 2/3 horse slant | livestock | — | — | horse | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-029 | Need a stock trailer | livestock | — | — | general_stock | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-030 | Need swing/slide or butterfly gates cattle trailer | livestock | — | — | cattle | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-031 | Looking for a Galyean | livestock | — | — | cattle | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-032 | Looking for a Star trailer | livestock | — | — | cattle | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-033 | Need a goat trailer | livestock | — | — | small_livestock | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-034 | Need a hog trailer | livestock | — | — | small_livestock | 0.90 | What type of animals are you hauling, and how many? | livestock |
| LP-035 | Looking for a 15 yard roll off package | roll_off | — | — | package_15yd | 0.90 | Are you looking for the trailer package, the bins, or both? | roll_off |
| LP-036 | Need a dumpster trailer | roll_off | — | — | — | 0.90 | Are you looking for the trailer package, the bins, or both? | roll_off |
| LP-037 | Do you sell just the dumpsters? | roll_off | — | — | bins_only | 0.95 | Are you looking for the trailer package, the bins, or both? | roll_off |
| LP-038 | Do you have any welding trailers | utility | welding | — | — | 0.90 | What are you hauling, and do you need sides or tool storage? | utility; welding |
| LP-039 | Looking for an enclosed car hauler | enclosed | race | — | — | 0.95 | What are you using it for, and what size do you need? | enclosed; race |
| LP-040 | Do you have any splicing trailers | enclosed | fiber | — | — | 0.95 | What are you using it for, and what size do you need? | enclosed; fiber |
| LP-041 | Do you have fiber optic trailers | enclosed | fiber | — | — | 0.95 | What are you using it for, and what size do you need? | enclosed; fiber |
| LP-042 | Looking for an office trailer *(fiber context)* | enclosed | fiber | — | — | 0.60 | Will this be for fiber / telecom work specifically, or a more general office or cooldown trailer? | enclosed; fiber |
| LP-043 | Need a cooldown trailer *(fiber context)* | enclosed | fiber | — | — | 0.60 | Will this be for fiber / telecom work specifically, or a more general office or cooldown trailer? | enclosed; fiber |
| LP-044 | Looking for a fuel tank | tank | — | — | diesel | 0.90 | Will this be for diesel or gasoline, and what capacity do you need? | tank; fuel |
| LP-045 | I need a tank trailer | tank | — | — | diesel | 0.75 | Will this be for fuel transport or for another liquid tank application? | tank; fuel |
| LP-046 | Do you have a trailer for gas | tank | — | — | gasoline | 0.90 | Will this be for diesel or gasoline, and what capacity do you need? | tank; fuel |
| LP-047 | Looking for a Aluma (Model Code) | — | — | aluminum | — | 0.85 | What type of trailer are you wanting in aluminum — utility, equipment, enclosed, or something else? | aluminum |
| LP-048 | Need a lightweight trailer | — | — | aluminum | — | 0.85 | What type of trailer are you wanting in aluminum — utility, equipment, enclosed, or something else? | aluminum |
| LP-049 | I'm looking for an aluminum trailer | — | — | aluminum | — | 0.85 | What type of trailer are you wanting in aluminum — utility, equipment, enclosed, or something else? | aluminum |
| LP-050 | I want a trailer that wont rust | — | — | aluminum | — | 0.85 | What type of trailer are you wanting in aluminum — utility, equipment, enclosed, or something else? | aluminum |
| LP-051 | Looking for a step deck | flatbed | — | — | step_deck | 0.90 | What are you hauling, and do you need standard flatbed or step deck? | flatbed |
| LP-052 | Looking for a platform trailer | flatbed | — | — | — | 0.90 | What are you hauling, and do you need standard flatbed or step deck? | flatbed |
| LP-053 | Looking for a hotshot trailer | flatbed | — | — | hotshot | 0.90 | What are you hauling, and do you need standard flatbed or step deck? | flatbed |
| LP-054 | Do you have any non CDL trailers? | flatbed | — | — | non_cdl | 0.80 | What are you hauling, and do you need standard flatbed or step deck? | flatbed |

---

## 4. Slot Rules

| slot_name | applies_to | required_level | fallback_if_unknown | recovery_prompt |
|---|---|---|---|---|
| haul_item | equipment; utility; tilt; flatbed | required_before_strong_recommendation | Ask what machine, vehicle, or material they are hauling | No problem — what are you hauling or planning to load? |
| haul_weight_lbs | equipment; car_hauler; utility; dump; tilt; flatbed | required_before_inventory_narrowing | Ask make/model of item being hauled | If you don't know the weight, what make and model is it? |
| haul_length_ft | equipment; car_hauler; utility; tilt; flatbed | important | Ask approximate size or footprint | About how long is it, even if it's just an estimate? |
| tow_vehicle | equipment; car_hauler; utility; dump; tilt; enclosed; livestock; roll_off; tank; flatbed | required_before_strong_recommendation | Ask SUV / half-ton / 3/4-ton / one-ton / dually | What are you towing with? Even a rough answer helps. |
| use_case | enclosed; fiber; offroad; race | required_before_meaningful_recommendation | Offer 3–4 common options as choices | Will you be using it for cargo, office space, camping, race support, or something else? |
| animal_type | livestock | required_before_recommendation | Ask cattle / horses / goats / mixed stock | What type of animals are you hauling? |
| animal_count | livestock | important | Ask for trailer length they think they need | About how many animals are you hauling at a time? |
| fuel_type | tank | required_before_recommendation | Ask diesel or gasoline | Will this be for diesel or gasoline? |
| package_scope | roll_off | required_before_recommendation | Ask package / bins only / both | Are you looking for the trailer package, the bins, or both? |

---

## 5. Question Flows (per category, in order)

### Equipment
1. What are you hauling? *(required)*
2. About how much does it weigh? *(required)*
3. About how long is it? *(required)*
4. What are you towing with? *(required)*
5. Do you want bumper pull or gooseneck? *(optional)*
6. Do you prefer ramps, drive-over fenders, or a deckover? *(optional)*

### Car Hauler
1. What vehicle are you hauling? *(required)*
2. About how much does it weigh? *(required)*
3. About how long is it? *(required)*
4. What are you towing with? *(required)*
5. Do you want an open car hauler or a covered setup? *(optional)*

### Utility
1. What are you hauling? *(required)*
2. About how much total weight are you hauling? *(required)*
3. What size trailer do you think you need? *(optional)*
4. Do you need tall sides, a gate, or tool storage? *(optional)*
5. What are you towing with? *(required)*

### Dump
1. What material are you hauling? *(required)*
2. About how much weight do you expect to carry? *(required)*
3. Are you looking for a scissor hoist, telescopic cylinder, or standard dump setup? *(optional)*
4. What are you towing with? *(required)*

### Tilt
1. What are you loading on the trailer? *(required)*
2. About how much does it weigh? *(required)*
3. Do you want full tilt or a stationary front deck with a tilt section? *(optional)*
4. What are you towing with? *(required)*

### Enclosed
1. What are you using it for? *(required)*
2. What size trailer do you need? *(required)*
3. What are you towing with? *(required)*
4. Do you need features like AC, windows, cabinets, or finished walls? *(optional)*

### Livestock
1. What type of animals are you hauling? *(required)*
2. How many animals are you hauling? *(required)*
3. What trailer length do you think you need? *(optional)*
4. Do you need any gate or divider preferences? *(optional)*
5. What are you towing with? *(required)*

### Roll Off
1. Are you looking for the trailer package, the bins, or both? *(required)*
2. What bin size do you need? *(required)*
3. What are you towing with? *(required)*

### Tank (Diesel)
1. Will this be for diesel or gasoline? *(required)*
2. What capacity do you need? *(required)*
3. What are you towing with? *(required)*

### Flatbed
1. What are you hauling? *(required)*
2. About how much does it weigh? *(required)*
3. Do you need a standard flatbed or a step deck? *(optional)*
4. What are you towing with? *(required)*
5. Are you trying to stay under a non-CDL setup? *(optional)*

### Fiber / Jobsite
1. Will this be for fiber splicing, a remote office, or a cooldown space? *(required)*
2. How many people need to work inside it? *(optional)*
3. Do you need AC, windows, work benches, or power? *(optional)*
4. What are you towing with? *(required)*

### Race Trailer
1. What vehicle are you hauling? *(required)*
2. What size trailer do you need? *(required)*
3. Do you need cabinets, work space, or living quarters? *(optional)*
4. What are you towing with? *(required)*

### Welding
1. What welding equipment are you carrying? *(required)*
2. About how much does the setup weigh? *(optional)*
3. What are you towing with? *(required)*

### Aluminum (modifier)
1. What type of trailer are you wanting in aluminum? *(required)*
2. What are you hauling? *(required)*
3. What are you towing with? *(required)*

### Offroad / Camping
1. Will you be using it for camping, overlanding, or gear hauling? *(required)*
2. Do you need sleeping space or just storage? *(optional)*
3. What are you towing with? *(required)*

---

## 6. Customer Explanations (Jargon)

| term | simple explanation | when to show |
|---|---|---|
| bumper pull | A bumper pull trailer hooks to a standard receiver hitch behind the truck. | When customer is comparing bumper pull vs gooseneck |
| gooseneck | A gooseneck trailer connects in the bed of the truck, which usually gives more stability and capacity than bumper pull. | When customer mentions heavy hauling or flatbed/hotshot |
| deckover | A deckover has the deck above the wheels, giving you full deck width for wider loads. | When customer needs full width or wide equipment |
| low-profile | A low-profile trailer sits lower to the ground, which can make loading easier. | When customer mentions lowboy or easier loading |
| GVWR | GVWR is the maximum total loaded weight the trailer is rated for. | When weight and payload come up |
| payload | Payload is how much cargo the trailer can carry after accounting for the trailer's own weight. | When discussing capacity |
| dovetail | A dovetail is a sloped rear section that helps make loading easier. | When customer asks about rear loading style |
| tilt | A tilt trailer tilts the deck so you can load without separate ramps in many cases. | When customer asks about loading convenience |
| V-nose | A V-nose enclosed trailer has an angled front that can help with storage and aerodynamics. | When customer asks about enclosed trailer shape |
| non-CDL setup | Customers often use this to mean they want to stay under certain weight thresholds, but exact legal requirements should be confirmed for their setup and location. | When customer mentions CDL concerns |

---

## 7. Objection Handling

| objection_id | customer_objection | recommended_response | follow_up_question |
|---|---|---|---|
| OBJ-001 | I do not know what size I need | No problem — if you tell me what you are hauling and about how much it weighs, I can usually narrow the size down pretty quickly. | What are you hauling? |
| OBJ-002 | I do not know what my truck can tow | I can help narrow options, but final towing capacity should be confirmed for your exact truck. What are you towing with? | What year, make, model, and truck size are you towing with? |
| OBJ-003 | That sounds too expensive | Understood. We can usually narrow things down by budget, size, and how often you will use it so you are not buying more trailer than you need. | Do you have a budget range you want to stay near? |
| OBJ-004 | I only need it once in a while | Got it. In that case it often makes sense to focus on the simplest trailer that safely fits what you are hauling. | What are you hauling, and how heavy is it? |
| OBJ-005 | I need something non-CDL | I can help point you in the right direction, but final legal compliance depends on the full truck and trailer setup and your local requirements. | What are you hauling, and what are you towing with? |
| OBJ-006 | I want the lightest trailer possible | That makes sense. Aluminum may be worth looking at if lightweight and corrosion resistance are priorities. | What type of trailer are you looking for in aluminum? |
| OBJ-007 | I have never bought one before | No problem at all — that is exactly what I can help with. We can keep it simple and start with what you are hauling. | What are you hauling? |

---

## 8. Safety Guardrails

| topic | safe_response_rule | customer_facing_safe_response | handoff_if_needed |
|---|---|---|---|
| towing_capacity | May help narrow options based on tow vehicle, but must NOT state exact towing capacity unless verified from exact truck configuration | I can help narrow options, but final towing capacity should be confirmed for your exact truck setup. | handoff if customer needs exact towing confirmation |
| payload_limits | May discuss approximate trailer classes and GVWR ranges, but should NOT guarantee payload fit without exact specs | Based on what you described, this points toward a heavier-duty option, but final payload fit should be confirmed from the actual trailer specs. | handoff if customer requests exact payload confirmation |
| cdl_thresholds | May discuss non-CDL language cautiously, but must NOT give legal advice or state compliance as fact | I can help point you in the right direction, but final CDL and legal compliance should be confirmed for your full setup and location. | handoff if customer needs legal confirmation |
| brake_requirements | Do NOT state exact brake-law requirements unless jurisdiction-specific data is available and verified | Brake requirements can vary, so final legal requirements should be confirmed for your location and setup. | handoff if customer needs regulatory confirmation |
| fuel_transport_compliance | Ask use case and fuel type but avoid giving compliance guarantees | I can help narrow the trailer type, but compliance for fuel transport should be confirmed based on your use case and local requirements. | handoff if customer needs compliance answer |
| inventory_availability | Do NOT state a unit is available now unless connected inventory is live and verified | I can show likely matches, and a team member can confirm current availability. | handoff if customer requests real-time availability |
| pricing_finality | May discuss starting prices or general ranges, but should NOT state a final out-the-door number unless verified | I can help with a starting point, and our team can confirm exact pricing and options. | handoff if customer requests final price |

---

## 9. Handoff Triggers

| trigger_id | trigger_type | trigger_phrase | handoff_action | priority |
|---|---|---|---|---|
| HAND-001 | customer_requests_person | talk to someone; call me; sales rep; person | immediate_sales_handoff | 10 |
| HAND-002 | specific_quote_request | quote me this; exact price; out the door | sales_quote_handoff | 10 |
| HAND-003 | live_inventory_request | is this available today; in stock right now | inventory_confirmation_handoff | 9 |
| HAND-004 | trade_in_request | trade in; value my trailer | trade_in_handoff | 9 |
| HAND-005 | financing_request | finance; monthly payment; approve me | finance_handoff | 9 |
| HAND-006 | service_or_parts | repair; service; fix; parts | service_or_parts_handoff | 9 |
| HAND-007 | frustration_or_looping | this is not helping; you keep asking; frustrated | priority_handoff | 10 |
| HAND-008 | high_risk_compliance_question | is this legal; CDL; towing capacity; fuel transport rules | safety_review_handoff | 8 |

---

## 10. Recommendation Logic

| output_template_id | category_id | minimum_required_info | recommended_output_template | handoff_rule |
|---|---|---|---|---|
| REC-001 | equipment | haul_item + haul_weight_lbs + tow_vehicle | Based on what you described, you are likely looking for an equipment trailer. Depending on the size and width of what you are hauling, you may be in the [size_range] range with around [capacity_range] GVWR. | handoff if customer wants exact inventory or quote |
| REC-002 | car_hauler | vehicle_type + haul_weight_lbs + tow_vehicle | It sounds like a car hauler is the right direction. From there we would narrow by deck length, weight rating, and whether you want an open or covered setup. | handoff if customer wants exact unit |
| REC-003 | utility | haul_item + tow_vehicle | This sounds like a utility trailer use case. The main things to narrow next are trailer size, side height, gate style, and total weight. | handoff if customer wants in-stock options |
| REC-004 | dump | haul_material + haul_weight_lbs + tow_vehicle | Based on the material and weight, you are likely in dump trailer territory. Next we would narrow by capacity, bed size, and whether you prefer scissor or telescopic style. | handoff if customer requests price or inventory |
| REC-005 | tilt | haul_item + haul_weight_lbs + tow_vehicle | A tilt trailer sounds like the right direction if easier loading is a priority. Next we would narrow by weight rating and whether you want full tilt or a stationary front deck. | handoff if customer wants exact unit |
| REC-006 | enclosed | use_case + cargo_size + tow_vehicle | It sounds like you are looking for an enclosed trailer. The best fit will depend mainly on your use case, target size, and whether you need interior features like AC, windows, or cabinets. | handoff if customer needs custom build guidance |
| REC-007 | livestock | animal_type + tow_vehicle | That points toward a livestock trailer. The next things to narrow are animal type, how many you are hauling, and any gate or divider preferences. | handoff if customer wants brand-specific recommendation |
| REC-008 | roll_off | package_scope + bin_size | It sounds like you are in the roll-off category. The first thing to confirm is whether you need the trailer package, the bins, or both, then we can narrow by bin size. | handoff if customer needs commercial quote |
| REC-009 | tank | fuel_type + tank_capacity + tow_vehicle | This sounds like a tank trailer or fuel support use case. The best direction will depend on fuel type, capacity, and how you plan to use it. | handoff if customer needs compliance answer |
| REC-010 | flatbed | haul_item + haul_weight_lbs + tow_vehicle | Based on what you described, you may be in flatbed or hotshot territory. The next step is narrowing by deck style, weight rating, and whether you are trying to stay in a non-CDL style setup. | handoff if customer needs exact legal guidance or stock unit |
| REC-011 | fiber | use_case + tow_vehicle | It sounds like you may need a specialty jobsite trailer. The key next step is whether this is for fiber splicing, office use, or cooldown support. | handoff if customer needs customization |
| REC-012 | aluminum | base_category + payload_need | It sounds like aluminum is the material direction you want. The next step is narrowing what type of trailer you want in aluminum. | handoff if customer needs model-specific comparison |
