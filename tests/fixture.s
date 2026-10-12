# Synthetisches Testprogramm, das typische IDO/libultra-Muster nachbildet.
        .set noreorder
        .set noat
        .text
        .globl entry
entry:                          # 0x80000400
        lui   $sp, 0x8010
        jal   main
        nop
1:      b     1b                # Endlosschleife (unbedingter Rücksprung)
        nop

main:
        addiu $sp, $sp, -24
        sw    $ra, 20($sp)
        lui   $a0, %hi(thread_fn)
        addiu $a0, $a0, %lo(thread_fn)  # Funktionszeiger
        jal   leaf
        nop
        jal   switcher
        li    $a0, 2
        lw    $ra, 20($sp)
        jr    $ra
        addiu $sp, $sp, 24

leaf:
        beqz  $a0, 2f
        nop
        addiu $v0, $a0, 1
2:      jr    $ra
        nop

switcher:
        sltiu $at, $a0, 3
        beqz  $at, 9f
        sll   $t6, $a0, 2
        lui   $at, %hi(jtbl)
        addu  $at, $at, $t6
        lw    $t6, %lo(jtbl)($at)
        jr    $t6
        nop
c0:     jr    $ra
        li    $v0, 10
c1:     jr    $ra
        li    $v0, 11
c2:     j     tail_target       # Tail-Call
        nop
9:      jr    $ra
        move  $v0, $zero

tail_target:
        mfc0  $t0, $12           # COP0 -> Hardwarebericht
        jr    $ra
        nop

thread_fn:
        addiu $sp, $sp, -32
        lui   $t0, 0xA460        # PI-Register
        lw    $t1, 0x10($t0)
        jr    $ra
        addiu $sp, $sp, 32

        .word 0, 0
orphan:                         # nirgends referenziert, nur per Lückensuche auffindbar
        addiu $sp, $sp, -16
        jr    $ra
        addiu $sp, $sp, 16

        .data
jtbl:   .word c0, c1, c2
