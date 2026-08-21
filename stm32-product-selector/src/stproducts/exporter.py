"""Per-workbook JSON export in the ST Sidekick format: ``products`` records.

One JSON file per output workbook (all 156, local and discovered alike),
matching the workbook's name. The envelope carries the selector's identity
and ``products`` is the flat record array Sidekick's ``rootTagPath`` points
at -- one record per part, self-sufficient (document/level/title repeated),
with ``values`` per parameter (nothing keyed-per-part lost) and a detailed,
multi-line plain-English ``descriptions`` map per parameter, keyed by
:attr:`stproducts.api.Column.key` (the same string used in the workbook
columns), with ST's own rendered label as the fallback.
"""

from __future__ import annotations

from .api import Grid
from .compose import ComposedSheet

#: Hand-written, multi-line plain-English descriptions of every parameter,
#: keyed by the API column key (the same string used in the workbooks and in
#: ``corrections.json``). Anything not listed falls back to ST's own composed
#: label from the grid metadata.
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "A/D Converters 12-bit | Number of A/D Converters typ": (
        "Number of 12-bit analogue-to-digital converters (ADC) integrated on the device (typical count).\n"
        "A 12-bit SAR (successive-approximation) converter is the standard ADC found on most STM32 parts; "
        "this figure counts the independent converter instances, not their input channels.\n"
        "Several channels share one converter through an input multiplexer, so the converter count and the "
        "channel count (see the paired 'Number of Channels' column) are independent facts."
    ),
    "A/D Converters 12-bit | Number of Channels typ": (
        "Total number of analogue-capable input pins the 12-bit ADC(s) can sample (typical).\n"
        "This counts every external channel across all 12-bit converter instances, including channels shared "
        "with other peripherals and the internal connections (reference voltage and temperature sensor).\n"
        "A channel is a pin wired to the ADC input matrix, whether the application measures on it or not."
    ),
    "A/D Converters 14-bit | Number of A/D Converters typ": (
        "Number of 14-bit analogue-to-digital converters integrated on the device (typical count).\n"
        "Fourteen-bit resolution is a higher-precision converter type offered on specific product lines; "
        "parts that do not carry such a converter leave this column unpopulated.\n"
        "Independent converter instances are counted separately."
    ),
    "A/D Converters 14-bit | Number of Channels typ": (
        "Total number of analogue input channels the 14-bit ADC(s) can sample (typical).\n"
        "The channel count covers every external input reachable through the converter's multiplexer, "
        "including pins shared with other functions.\n"
        "Only parts integrating a 14-bit converter populate this column."
    ),
    "A/D Converters 16-bit | Number of A/D Converters typ": (
        "Number of 16-bit analogue-to-digital converters on the device (typical count).\n"
        "Sixteen-bit resolution is implemented as a sigma-delta converter on the parts that offer it, giving "
        "very high precision at lower sample rates than a fast SAR converter.\n"
        "Only parts carrying a 16-bit converter populate this column."
    ),
    "A/D Converters 16-bit | Number of Channels typ": (
        "Total number of analogue input channels the 16-bit converter(s) can sample (typical).\n"
        "As with the lower-resolution converters, the figure includes every input pin reachable through the "
        "input multiplexer.\n"
        "Only parts integrating a 16-bit converter populate this column."
    ),
    "Additional Interfaces": (
        "Extra wired communication interfaces that the dedicated count columns (USART, UART, I2C, SPI and "
        "companions) do not already total.\n"
        "This is the remainder of the connectivity fabric on top of the serial-peripheral counts, so treat it "
        "as a complement: blank here does not prove an interface is absent, only that ST's selector expresses "
        "nothing beyond the categorised sets.\n"
        "The specific interfaces and their counts are what the datasheet's peripheral overview states for the part."
    ),
    "Advanced Motor Control Timers": (
        "Number of advanced-control timers credited with motor-control capability (typical).\n"
        "These timers generate the complementary centre-aligned PWM waveforms, mandatory dead-time insertion, "
        "and the fault / brake channels used to drive motor phases and read back fault conditions.\n"
        "Regular general-purpose timers are counted in their own columns and are not included here."
    ),
    "Buy On Line": (
        "Whether ST sells the part through its public online storefront (the 'buy online' channel).\n"
        "The marker reflects commercial availability on st.com; it says nothing about distributors, stock, "
        "or delivery lead times.\n"
        "It is storefront metadata and is not stated in the datasheet."
    ),
    "CAN (2.0)": (
        "Number of classic CAN 2.0B controllers (bxCAN) implemented on the device (typical).\n"
        "The bxCAN block handles arbitration, message filtering and error handling for the Controller Area "
        "Network bus, and accepts both 2.0A (11-bit identifier) and 2.0B (29-bit identifier) frames.\n"
        "Parts built around the newer FDCAN IP are counted under CAN (FD) instead, so a given part normally "
        "figures in only one of the two columns."
    ),
    "CAN (FD)": (
        "Number of CAN-FD-capable controllers (FDCAN) on the device (typical).\n"
        "CAN FD extends classic CAN with a variable bit-rate data phase (up to 8 Mbit/s in the data segment) "
        "and a stronger 32-bit CRC, while keeping the 1 Mbit/s arbitration phase of classic CAN.\n"
        "A part with FDCAN still transmits classic 2.0 frames on the same bus; the two columns never "
        "double-count a single controller."
    ),
    "CCM RAM (I/D) (kByte) typ": (
        "Core-coupled memory capacity in KiB, split between the instruction (I) and data (D) blocks (typical).\n"
        "CCM RAM is accessible from the CPU with zero-wait-state deterministic latency and is not reachable by "
        "DMA or the other bus masters, which is why its capacity is tracked apart from the general SRAM.\n"
        "The reported figure is the combined I + D capacity."
    ),
    "Co-Processor frequency (MHz) max": (
        "Maximum clock frequency, in MHz, at which the secondary co-processor core can run.\n"
        "The co-processor is a companion subsystem (for example a Cortex-M0+ or Cortex-M4) embedded to run "
        "low-power background, radio or real-time tasks alongside the main core.\n"
        "The value is the co-processor's own ceiling; an application is normally clocked below it."
    ),
    "Co-Processor type": (
        "Type of the secondary processor core(s) embedded on the device, for example an Arm Cortex-M0+ or "
        "Cortex-M4 companion.\n"
        "The co-processor runs independently of the main core and typically handles power management, radio "
        "protocol stacks, or a real-time watchdog role.\n"
        "A blank value means the part is single-core or carries no companion."
    ),
    "Comparator": (
        "Number of built-in analogue comparators on the device (typical).\n"
        "Comparators can trigger interrupts or break signals and are frequently combined into windowed "
        "comparison for over- and under-voltage supervision without CPU involvement.\n"
        "The count is of independent comparator instances, not of the pins they can monitor."
    ),
    "Connectivity supported": (
        "Which wired and wireless communication standards the device implements, listed by standard name.\n"
        "This is the high-level connectivity bill of materials (Ethernet, USB, CAN, and so on); individual "
        "peripheral instance counts are reported in their own dedicated columns.\n"
        "A blank value means the part offers no connectivity beyond the counted serial peripherals."
    ),
    "Core": (
        "CPU core family of the device, e.g. Arm Cortex-M4, Arm Cortex-M7, or a dual Cortex-A/M combination "
        "on application processors.\n"
        "The core together with its FPU and cache configuration largely defines the compute class and the "
        "instruction set of the part.\n"
        "Dual-core parts show the primary core here; companion cores are described by Co-Processor type."
    ),
    "Cryptography": (
        "Hardware cryptographic acceleration features of the device, normally a dedicated crypto processor "
        "supporting AES and, on parts that carry it, 3DES, SHA, HMAC and public-key arithmetic.\n"
        "A hardware crypto block offloads encryption and hashing from the CPU and typically keeps key material "
        "outside ordinary application memory.\n"
        "This column names the supported algorithms, not a count."
    ),
    "D/A Converters (12-bit) typ": (
        "Number of 12-bit digital-to-analogue converters (DAC) integrated on the device (typical).\n"
        "The DAC turns a digital sample value into an analogue voltage on a dedicated output pin, commonly used "
        "for audio, waveform, or set-point generation.\n"
        "Some parts integrate two independent channels; the figure counts converter instances."
    ),
    "DRAM support typ": (
        "External DRAM families the memory controller can drive (relevant to application processors), for "
        "example LPDDR2, LPDDR3 and DDR3L under an integrated DRAM controller.\n"
        "It states the interface families, not the maximum capacity or the number of memory banks.\n"
        "Blank means the part exposes no external DRAM controller."
    ),
    "Data E2PROM (B) nom": (
        "Embedded EEPROM capacity in bytes (nominal).\n"
        "EEPROM bytes can typically be erased and written at a granularity far smaller than a Flash sector, "
        "which suits calibration, configuration or security data that changes often.\n"
        "Data EEPROM is offered by specific low-power and derivative STM32 families alongside their Flash."
    ),
    "Display controller": (
        "Which display interfaces the part integrates, for example a parallel RGB LCD-TFT controller or a "
        "MIPI-DSI transmitter.\n"
        "A built-in TFT controller drives a parallel-RGB panel directly; a DSI block carries the same frame "
        "data over a serial lane link to a display module. Both offload the CPU from the frame-refresh duty.\n"
        "Blank indicates no display-driving hardware is integrated."
    ),
    "Dual-bank Flash": (
        "Whether the program Flash is organised as two independent banks (yes/no).\n"
        "A dual-bank layout enables read-while-write: code can keep executing from one bank while the other is "
        "erased or programmed, which is what live (in-field-over-the-air) firmware updates rely on.\n"
        "A single-bank part must suspend instruction fetches while the Flash controller programs."
    ),
    "Ethernet": (
        "Ethernet capability of the part and its speed class, typically a single 10/100 Mbit/s Ethernet MAC.\n"
        "An embedded MAC handles the media-access layer; designs still add an external PHY transceiver for the "
        "physical layer.\n"
        "This column states capability rather than a connection count."
    ),
    "Ethernet ports typ": (
        "Number of Ethernet MAC ports the part integrates (typical).\n"
        "Most devices carry a single 10/100 MAC; multiple-port parts are uncommon.\n"
        "Blank means the part has no integrated Ethernet MAC."
    ),
    "External Memory Interfaces": (
        "The external memory bus types the part exposes, for example a parallel FSMC/FMC controller, Quad-SPI, "
        "or Octo-SPI serial interface.\n"
        "These let code or data execute from external NOR, NAND, PSRAM or DRAM mapped into the memory space.\n"
        "The column lists interface families rather than pin or bus widths."
    ),
    "FPU": (
        "Floating-point unit(s) present on the CPU, e.g. the single-precision VFPv4 hardware FPU found on most "
        "Cortex-M4/M33/M7 parts.\n"
        "A hardware FPU executes float arithmetic in silicon instead of soft-float library emulation, which "
        "matters for DSP and control-loop throughput.\n"
        "Blank means no hardware FPU; float is computed in software."
    ),
    "Flash Size (kB) (Prog)": (
        "Program memory capacity in KiB: the size of the embedded Flash used to store firmware.\n"
        "Selected density of the part SKU (higher-density variants are listed separately in the datasheet).\n"
        "Values are in kilobytes with binary meaning (1 KiB = 1024 bytes)."
    ),
    "Flash Support typ": (
        "External Flash memory families the memory interface can address, for example NOR operated through "
        "FSMC/FMC or serial NOR through Quad/Octo-SPI.\n"
        "Complement of DRAM support; it lists what external program/expanded storage the controller can reach.\n"
        "This does not describe the embedded program Flash (see Flash Size)."
    ),
    "General Description": (
        "ST's one-paragraph marketing summary of the device, taken verbatim from the product page.\n"
        "It positions the part in the portfolio, naming the target market and the headline differentiating "
        "features, and is prose rather than a structured field.\n"
        "No data is derived from it; treat it as human-readable context."
    ),
    "Graphic accelerator": (
        "Whether the part integrates a graphics processing block (2D engine, GPU, or display-composition "
        "accelerator, e.g. on application-processor parts).\n"
        "An accelerator moves drawing, blending and compositing work off the CPU, which UI code benefits from "
        "directly.\n"
        "This column is a presence marker; the exact feature set (chroma-key, rotation, blending) lives in the datasheet."
    ),
    "I/Os (High Current)": (
        "Number of general-purpose I/O pins rated for high sink/source current (typically the 20 mA drive class).\n"
        "High-current GPIO pins can switch LEDs, relays or buzzer loads directly, avoiding an external buffer.\n"
        "Only the high-drive pins of the given package are counted."
    ),
    "I2C typ": (
        "Number of I2C (Inter-Integrated Circuit) interfaces on the device (typical).\n"
        "Each I2C peripheral is a multi-master two-wire (SCL/SDA) controller that can also act as a slave, "
        "driven over 400 kHz in most parts.\n"
        "The count is of I2C peripherals, not of the individual busses they may serve at one time."
    ),
    "I2S typ": (
        "Number of I2S (Inter-IC Sound) audio interfaces on the device (typical).\n"
        "I2S carries stereo digital audio between the MCU and external audio codecs or digital microphones.\n"
        "On parts where I2S re-uses SPI peripherals, ST still reports the usable audio links here."
    ),
    "I3C typ": (
        "Number of I3C (Improved Inter-Integrated Circuit) interfaces on the device (typical).\n"
        "I3C is the modernisation of I2C: backwards compatible, faster (up to 12.5 MHz), with hot-join and "
        "interrupt support that suits sensor hubs.\n"
        "Each I3C peripheral provides one controller port plus slave capability."
    ),
    "ITCM/DTCM RAM (kB)": (
        "Tightly-coupled memory capacity in KiB: the instruction and data TCM blocks beside the CPU, found on "
        "Cortex-M7-class devices.\n"
        "TCM provides deterministic zero-wait-state access for critical code and data and sits directly on the "
        "core rather than the shared AHB fabric.\n"
        "The reported figure is the combined ITCM + DTCM capacity."
    ),
    "Integrated op-amps": (
        "Number of operational amplifiers integrated on the device (typical).\n"
        "Embedded op-amps let an application condition analogue signals (gain, filtering, buffering) without "
        "placing an external amplifier.\n"
        "Depending on the part they can be configured as follower or programmable-gain amplifiers."
    ),
    "Junction Temperature (°C) max": (
        "Maximum junction temperature in degrees Celsius the silicon is qualified to sustain.\n"
        "Junction temperature (Tj) is the temperature of the die itself; it is driven by ambient temperature "
        "plus the part's own power dissipation.\n"
        "The ambient operating range is stated separately under Operating Temperature."
    ),
    "Junction Temperature (°C) min": (
        "Minimum junction temperature in degrees Celsius the silicon is qualified for.\n"
        "Like the upper bound, the lower junction limit guarantees correct electrical behaviour down to this "
        "die temperature.\n"
        "Together with the maximum it defines the full qualified die-temperature window."
    ),
    "L1 Cache (kB) typ": (
        "Level-1 cache capacity in KiB (typical), normally split between an instruction cache and a data cache "
        "on Cortex-M7 and application-processor parts.\n"
        "The L1 cache sits closest to the core and absorbs repeated memory accesses at full speed.\n"
        "The reported figure is the combined instruction and data L1 capacity."
    ),
    "L2 Cache (kB) typ": (
        "Level-2 cache capacity in KiB (typical), a larger cache behind the L1 that serves the whole core "
        "memory system.\n"
        "L2 is found on high-end parts (for example the 512 KiB L2 cache of the STM32H7 multimedia line).\n"
        "Access to L2 is slower than L1 but faster than main SRAM or external memory."
    ),
    "LIN-UART typ": (
        "Number of UART peripherals that implement the LIN (Local Interconnect Network) protocol capability "
        "(typical).\n"
        "LIN is a low-cost single-wire bus used mainly in automotive body and comfort electronics; the UART "
        "peripheral provides the master/slave framing and break handling.\n"
        "Plain UARTs without LIN framing are counted in the UART column."
    ),
    "Longevity Commitment (yr) typ": (
        "Duration, in years, that ST commits to keeping the part orderable, under the ST longevity programme "
        "(typically 10 or 15 years from launch).\n"
        "The commitment is a supply guarantee window, not a projection of technical product lifetime.\n"
        "Combine with Longevity Starting Date to know the guaranteed availability horizon."
    ),
    "Longevity Starting Date": (
        "The date from which the longevity commitment period begins to count (usually the product launch date).\n"
        "Once a part moves toward end-of-life, availability is still guaranteed until this date plus the "
        "committed duration.\n"
        "It is expressed as a calendar date in the product records."
    ),
    "Marketing Status": (
        "Commercial lifecycle state of the part at ST, for example Active (recommended for new designs), "
        "NRND (not recommended for new designs) or end-of-life/discontinued.\n"
        "The status is ST's market classification and is independent of the datasheet's electrical claims.\n"
        "It informs whether a new design should lock in on this part or prefer a successor."
    ),
    "NPU AI/NN Hardware Accelerator": (
        "Whether the part integrates a neural-network / AI accelerator (NPU), found on AI-targeted lines.\n"
        "An NPU executes quantised CNN and transformer workloads at far better performance-per-watt than the "
        "CPU alone.\n"
        "This column is a presence marker; throughput and supported networks are specified in the datasheet."
    ),
    "Number of A/D Converters (10-bit Channels) typ": (
        "Number of input channels a 10-bit converter instance can reach (typical).\n"
        "This is ST's shorthand on some older lines for the channel footprint of a 10-bit ADC block.\n"
        "It qualifies converter channel capability rather than giving a converter count."
    ),
    "Number of A/D Converters (12-bit Channels) typ": (
        "Number of input channels a 12-bit converter instance can reach (typical).\n"
        "Shorthand used on some lines for the channel footprint of a 12-bit ADC block.\n"
        "It qualifies converter channel capability rather than giving a converter count."
    ),
    "Number of Cores nom": (
        "Number of CPU cores integrated on the device (nominal): 1 for single-core parts, 2 for dual-core "
        "devices such as a Cortex-M7 + Cortex-M4 combination or a dual-Cortex-A application processor.\n"
        "Dual-core parts apply the cores to asymmetric workloads, with the types shown in Core and "
        "Co-Processor type.\n"
        "Nominal refers to the shipped configuration."
    ),
    "On-chip SRAM (kB) typ": (
        "Embedded static RAM capacity in KiB (typical), totalled across the SRAM blocks the main core can "
        "address.\n"
        "It excludes the core-coupled CCM regions tracked separately, and excludes cache memory.\n"
        "This is the working-data memory available to firmware without external memory."
    ),
    "Operating Frequency (MHz)": (
        "Maximum CPU clock frequency in MHz that the device is qualified to run at.\n"
        "Exceeding the rated frequency is outside the electrical specification and invalidates the published "
        "timing values.\n"
        "The number reflects the highest speed grade of the given SKU."
    ),
    "Operating Temperature (°C) max": (
        "Maximum ambient operating temperature in degrees Celsius.\n"
        "Ambient is the air temperature surrounding the device while the part stays within specification; the "
        "die itself may run hotter (see Junction Temperature).\n"
        "Parts are offered in temperature grades (for example -40/85 °C or -40/105 °C); this column states the "
        "high end of the guaranteed grade."
    ),
    "Operating Temperature (°C) min": (
        "Minimum ambient operating temperature in degrees Celsius.\n"
        "Together with the maximum it bounds the grade the SKU is guaranteed over (for example -40 to 85 °C).\n"
        "Operation below the limit is outside the specification."
    ),
    "Other timer functions": (
        "Timer features that fall outside the counted 8/16/32-bit general-purpose set, listed by name "
        "(for example low-power timers, independent watchdogs, RTC alarm resources).\n"
        "This is a capability list rather than a count.\n"
        "It lets a designer see at a glance which specialised timing blocks a part carries."
    ),
    "Output Power (dBm) (Step) typ": (
        "Number of distinct, configurable RF transmit-power levels the radio supports (typical).\n"
        "Wireless parts expose a set of programmable output powers between the Output Power min and max "
        "figures.\n"
        "The step count matters for output-power management where battery life or regulations bound the "
        "transmit budget."
    ),
    "Output Power (dBm) max": (
        "Maximum configurable RF transmitter output power in dBm, specified at the reference antenna.\n"
        "Regulatory and link-budget work both start from this ceiling.\n"
        "Applicable to wireless line parts (STM32WB / STM32WL)."
    ),
    "Output Power (dBm) min": (
        "Minimum configurable RF transmitter output power in dBm, the lowest programmable setting.\n"
        "Selecting a low output power reduces current draw and interference where the link margin allows.\n"
        "Applicable to wireless line parts (STM32WB / STM32WL)."
    ),
    "PCIe": (
        "Number of PCI Express interfaces integrated on the part (relevant to application-processor parts).\n"
        "A PCIe root port lets the processor attach high-speed peripherals, storage or accelerators.\n"
        "Blank means the part exposes no PCIe controller."
    ),
    "PCIe type": (
        "PCIe generation and lane configuration, for example Gen3 x1 or x2.\n"
        "A higher generation doubles the per-lane bit rate, while more lanes scale the aggregate bandwidth.\n"
        "Both determine the practical throughput the PCIe port can sustain."
    ),
    "Package": (
        "Package type(s) in which the part is offered, for example LQFP48, UFBGA, WLCSP or TFBGA.\n"
        "One silicon die is often available in several packages with different pin counts and footprints.\n"
        "The package name encodes the form factor, pin count and, on many parts, the body size."
    ),
    "Part Number": (
        "ST orderable part number: the unique ordering code that identifies the exact SKU.\n"
        "Every row in the workbook is keyed on this value.\n"
        "The full part number carries the package and temperature-grade suffixes in ST's naming scheme."
    ),
    "RAM Size (kB)": (
        "Embedded RAM capacity in KiB as ST lists it in the product summary.\n"
        "It overlaps conceptually with On-chip SRAM; where the two appear together treat this as the "
        "datasheet's own headline RAM figure.\n"
        "The value is what the part's RAM family section reports."
    ),
    "RF frequency (MHz) typ": (
        "Radio operating frequency band(s) in megahertz, for example 2400 for the 2.4 GHz band of wireless "
        "parts or 868/915 for sub-GHz links.\n"
        "It states the licensed-free band the radio is tuned to.\n"
        "Dual-band radios report the several bands comma-separated."
    ),
    "RX current (mA) typ": (
        "Radio receiver current draw in milliamperes while listening (typical).\n"
        "Receive current dominates battery drain in listening-heavy wireless designs, so it sizes the average "
        "current budget.\n"
        "Measured at nominal supply voltage and default radio configuration."
    ),
    "RX sensitivity (dBm) typ": (
        "Radio receiver sensitivity in dBm (typical): the weakest signal the receiver can still decode.\n"
        "Lower (more negative) dBm values mean better sensitivity and a larger reliable range.\n"
        "It is normally stated at a reference packet-error rate and data rate."
    ),
    "SMPS": (
        "Whether the part integrates a switched-mode power-supply option (an internal step-down SMPS "
        "regulator) for the digital core.\n"
        "An SMPS converts the supply rail far more efficiently than a linear LDO, significantly reducing "
        "run-mode and system current.\n"
        "Where fitted it needs a small external inductor; its benefit is lower power than a pure LDO design."
    ),
    "SPI typ": (
        "Number of SPI (Serial Peripheral Interface) ports on the device (typical).\n"
        "Each SPI is a full-duplex synchronous serial controller, usually also usable in receiver-only or "
        "slave modes.\n"
        "Some SPI instances are multiplexed with I2S; the audio capability is still counted where usable."
    ),
    "Secure Boot spec": (
        "Whether the part supports a secure-boot specification, for example a PSA (Platform Security "
        "Architecture) certified secure-boot implementation.\n"
        "Secure boot cryptographically authenticates firmware before it executes, anchoring trust in an "
        "immutable root of trust.\n"
        "It appears on secure/trusted product lines and is a presence marker."
    ),
    "Security Functions": (
        "Security features beyond pure cryptography: secure storage, read protection, tamper detection, secure "
        "RTC, unique device identification and TrustZone-enabled isolation.\n"
        "These capabilities support trusted boot, OTA integrity and anti-counterfeiting measures.\n"
        "Cipher and hash algorithms themselves are listed under Cryptography instead."
    ),
    "Standby Current (µA) typ": (
        "Supply current in microamperes when the device is in its standby / lowest-power state (typical).\n"
        "Standby power is dominated by leakage and the wakeup logic, since no code is running.\n"
        "Typical values are quoted at room temperature and nominal supply; worst-case figures are higher."
    ),
    "Supply Current (µA) (@ Lowest Power) typ": (
        "Supply current in microamperes with the device held in its lowest-power mode (typical).\n"
        "This normally corresponds to standby or stop-with-RTC-retention rather than full run mode.\n"
        "Use it as a sizing aid for always-on applications, not as a guarantee over the whole temperature range."
    ),
    "Supply Current (µA) (Run Mode (per MHz)) typ": (
        "Run-mode supply current drawn per megahertz of clock, in µA/MHz (typical).\n"
        "Normalising the active current per MHz lets a design scale the estimate with the programmed clock "
        "frequency.\n"
        "Multiply by the target CPU frequency for a first-order run-mode current estimate; real draw varies "
        "with code and I/O activity."
    ),
    "Supply Voltage (V) max": (
        "Maximum supply voltage in volts that may be applied to the device's main VDD domain.\n"
        "Operating above this ceiling is outside the electrical specification.\n"
        "Where separate rails (VDDIO, VDDA or a radio supply) exist, the general figure may be narrowed by the "
        "most restrictive domain."
    ),
    "Supply Voltage (V) min": (
        "Minimum supply voltage in volts the device requires to operate within specification.\n"
        "Parts with internal regulators state the input-rail minimum; some families add footnoted lower limits "
        "(for example with the BOR detector disabled).\n"
        "Where the datasheet qualifies the figure by condition, the selector keeps ST's published value."
    ),
    "TRNG typ": (
        "Whether the part integrates a hardware true random-number generator (TRNG) (typical).\n"
        "A TRNG samples physical entropy to produce genuinely unpredictable numbers for key material and "
        "authentication, unlike a software PRNG.\n"
        "The column is a presence marker."
    ),
    "TX current (mA) (@ 0dBm) max": (
        "Radio transmit current in milliamperes at an output power of 0 dBm (maximum).\n"
        "Transmit current is the burst peak drawn by the power-amplifier chain while a packet is sent.\n"
        "Quoted at 0 dBm so a battery design can budget for constant-duty traffic."
    ),
    "Target Application": (
        "Applications ST positions the part for, for example motor control, smart home, medical, industrial "
        "sensing or wearables.\n"
        "This is a marketing classification reflecting the peripheral set, power and cost profile.\n"
        "It is guidance: any STM32 can run arbitrary workloads and this is not a hard constraint."
    ),
    "Timers (16-bit) typ": (
        "Number of general-purpose 16-bit timers on the device (typical).\n"
        "General-purpose timers provide timebases, output compare, input capture, PWM generation and simple "
        "encoder coupling.\n"
        "Advanced and low-power timers are not counted here."
    ),
    "Timers (32-bit) typ": (
        "Number of general-purpose 32-bit timers on the device (typical).\n"
        "32-bit timers are used where long timebases or high-resolution capture windows are needed.\n"
        "Advanced and low-power timers are not counted here."
    ),
    "Timers (8-bit) typ": (
        "Number of 8-bit timers on the device (typical).\n"
        "The 8-bit base timers provide simple timebase or clock functions at low complexity.\n"
        "General-purpose 16/32-bit and advanced timers are counted in their own columns."
    ),
    "Touch sensing FW library": (
        "Whether the touch-sensing firmware library is supported on the part (yes/no).\n"
        "Software capacitive touch implements buttons, wheels and sliders on selected GPIO pins without "
        "dedicated touch hardware.\n"
        "Support depends on the shape-analogue-capable GPIO set of the part plus the library."
    ),
    "UART typ": (
        "Number of UART (asynchronous serial) interfaces on the device (typical).\n"
        "A UART transfers bytes over a single wire pair (TX/RX) with no clock line.\n"
        "USART peripherals that also provide UART mode are counted under USART, not here."
    ),
    "USART typ": (
        "Number of USART (synchronous + asynchronous serial) interfaces on the device (typical).\n"
        "A USART adds a clock line and can operate in synchronous, smartcard, IrDA or LIN modes depending on "
        "the family.\n"
        "The count covers the USART peripherals specifically, not the UART-only instances."
    ),
    "LPUART typ": (
        "Number of LPUART (low-power UART) interfaces on the device.\n"
        "An LPUART is a serial interface in the same TX/RX asynchronous family as a UART, clocked from the "
        "low-power domain so it can wake the part or stay receive-active in Stop modes, at lower maximum "
        "baud rates than the general-purpose USART/UART instances."
    ),
    "USB 2.0 typ": (
        "Number of USB 2.0 interfaces on the device (typical).\n"
        "USB 2.0 covers full-speed (12 Mbit/s) and high-speed (480 Mbit/s) device, host and OTG controller "
        "configurations.\n"
        "The roles a specific part supports are detailed in the USB Type column."
    ),
    "USB 3.0": (
        "Number of USB 3.x interfaces on the part (relevant to application processors).\n"
        "USB 3.0 / SuperSpeed provides a nominally 5 Gbit/s link where integrated.\n"
        "Blank means the part is limited to USB 2.0 provision."
    ),
    "USB Type": (
        "Which USB roles and speed classes the device supports, for example device, host, OTG, or DRD, and the "
        "corresponding interface type.\n"
        "Roles determine whether the MCU behaves as a peripheral, masters the bus, or switches between the two.\n"
        "The physical connector is chosen by the application; this column describes the controller's role set."
    ),
    "Video HW accelerator": (
        "Whether the part integrates a dedicated video-processing block (for example an H.264 encoder/decoder "
        "on video application-processor parts).\n"
        "A video accelerator offloads encode/decode and scaling from the CPU for camera and display pipelines.\n"
        "The column is a presence marker; codec support details live in the datasheet."
    ),
}


def export_sheet_json(
    document: str, grid: Grid, layout_keys: list[str], composed: ComposedSheet
) -> dict:
    """The Sidekick-shaped ``document``/``products`` export for one workbook.

    The envelope carries the selector's identity and ``products`` is the
    record array Sidekick's ``rootTagPath`` points at. Every product is one
    flat, self-sufficient record -- ``document``/``level_id``/``level_title``
    are repeated on it because Sidekick never sees anything outside the
    array.

    ``values`` holds the part's cells exactly as written to the xlsx
    (``ComposedCell.value``); blank cells are ``""``. ``descriptions``
    carries the detailed multi-line explanation of every parameter from
    :data:`PARAMETER_DESCRIPTIONS`, falling back to ST's own rendered column
    label for anything not curated. ``features`` lists the parameter keys so
    each record carries its own tags. ``url`` is the part's ST product page
    (``.../en/microcontrollers-microprocessors/<part_number>.html``), which
    is the one deep link a selector record can truthfully carry, and
    ``text_helper`` is a retrieve-ready summary sentence.
    """
    by_key = grid.by_key()
    descriptions = {}
    for key in layout_keys:
        # Curated text first; ST's own rendered label next; the tool's own
        # extra columns (no Column metadata) carry their key, which is the
        # label they were given.
        descriptions[key] = PARAMETER_DESCRIPTIONS.get(key) or (
            by_key[key].label if key in by_key else key
        )
    part_order = [p for p in grid.part_numbers if p in composed.parts]
    products: list[dict] = []
    for part in part_order:
        cell = composed.parts[part].cells
        values = {key: cell[key].value for key in layout_keys if key in cell}
        products.append(
            {
                "product_id": part,
                "document": document,
                "level_id": grid.level_id,
                "level_title": grid.level_title,
                "part_number": part,
                "semantic_type": "product_selector",
                "features": layout_keys,
                "url": (
                    "https://www.st.com/en/microcontrollers-microprocessors/"
                    f"{part.lower()}.html"
                ),
                "text_helper": (
                    f"{part}, part of the {document} product selector "
                    f"({len(part_order)} parts, {len(layout_keys)} parameters)."
                ),
                "values": values,
                "descriptions": descriptions,
            }
        )
    return {
        "document": document,
        "level_id": grid.level_id,
        "level_title": grid.level_title,
        "product_count": len(products),
        "products": products,
    }