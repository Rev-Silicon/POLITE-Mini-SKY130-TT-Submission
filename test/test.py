import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, Timer


@cocotb.test()
async def test_project(dut):

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    # Functional simulation clock: 100 kHz.
    # This is not a 66 MHz timing test.
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    async def tick(instruction=0, data=0):
        # Apply signals before the next active clock edge.
        await FallingEdge(dut.clk)
        dut.ui_in.value = data & 0xFF
        dut.uio_in.value = instruction & 0xFF

        await RisingEdge(dut.clk)
        await Timer(1, unit="us")

        return int(dut.uo_out.value)

    async def reset_chip():
        await FallingEdge(dut.clk)
        dut.rst_n.value = 0
        dut.ui_in.value = 0
        dut.uio_in.value = 0
        dut.ena.value = 1

        await ClockCycles(dut.clk, 5)
        await Timer(1, unit="us")

        assert int(dut.uo_out.value) == 0, "Reset output incorrect"
        assert int(dut.uio_out.value) == 0, "uio_out must be zero"
        assert int(dut.uio_oe.value) == 0, "uio_oe must be zero"

        dut.rst_n.value = 1
        await tick()

    async def issue(instruction, data=0):
        # One clock to register the pad inputs.
        await tick(instruction, data)

        # One clock to execute the registered instruction.
        await tick()

    async def run_mac(rows, columns):
        await issue(0x10, rows)      # LOAD
        await issue(0x20, columns)   # RUN

        # Allow the pending multiply-accumulate to complete.
        for _ in range(3):
            await tick()

    async def read_dump():
        await issue(0x30)            # DUMP_LOAD

        nibbles = []

        # Find the first valid dump output.
        for _ in range(12):
            output = await tick()
            if output & 0x40:
                nibbles.append(output & 0x0F)
                break

        assert nibbles, "No dump output detected"

        # Output changes every two enabled clock cycles.
        for index in range(1, 24):
            await tick()
            output = await tick()

            assert output & 0x40, (
                f"Dump ended prematurely at nibble {index}: "
                f"output=0x{output:02X}"
            )

            assert (output & 0xB0) == 0, (
                f"Unexpected debug, illegal or overflow flag: "
                f"output=0x{output:02X}"
            )

            nibbles.append(output & 0x0F)

        # Reconstruct four signed 24-bit accumulators.
        values = []

        for pe in range(4):
            value = 0

            for nib in range(6):
                value |= nibbles[pe * 6 + nib] << (4 * nib)

            if value & 0x800000:
                value -= 0x1000000

            values.append(value)

        return values

    async def check_dump(label, expected):
        actual = await read_dump()
        dut._log.info("%s: expected=%s actual=%s",
                      label, expected, actual)

        assert actual == expected, (
            f"{label}: expected {expected}, received {actual}"
        )

    # TEST 1: Positive operands
    await reset_chip()
    await run_mac(0x12, 0x34)
    await check_dump("Positive multiplication", [3, 4, 6, 8])

    # TEST 2: Two multiply-accumulate operations
    await reset_chip()
    await run_mac(0x12, 0x34)
    await run_mac(0x12, 0x34)
    await check_dump("Accumulation", [6, 8, 12, 16])

    # TEST 3: Signed int4 operands
    # Rows: high=-2, low=3
    # Columns: high=4, low=-3
    await reset_chip()
    await run_mac(0xE3, 0x4D)
    await check_dump("Signed multiplication", [-8, 6, 12, -9])

    # TEST 4: Clear all accumulator values
    await reset_chip()
    await run_mac(0x12, 0x34)
    await issue(0x58)                # CLEAR bit 3

    for _ in range(3):
        await tick()

    await check_dump("Clear accumulators", [0, 0, 0, 0])

    dut._log.info("All four POLITE Mini functional cases passed")
