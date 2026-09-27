;=============================================================================
; game_cellcache.asm - VRAM shape-cell cache,  (pure-solid narrow cells)
;
;   The game is CPU-bound when played (water tick ~8.2 vbl vs hold 5): the
;   tick is dominated by polygon DECODE, and 70-96% of top-level draws recur.
;   This caches a recurring draw (off, zoom, x-parity, video-bank) as a
;   pre-rendered cell in free VRAM; every recurrence is ONE clipped
;   BLT_BSTENCIL blit instead of decode+raster.
;
;   Executable spec = tools/validate_cellcache.py (BYTE-IDENTICAL on all 6
;   parts vs the uncached engine; run with for this  policy).
;   Full design: docs/PLAN-2026-06-10-cellcache.md. 1 policy:
;     * bake only when the centre probe is CLEAN (extents away from every
;       screen edge -> the render was not clipped; group headers lie, so
;       this is decided from rendered extents only)
;     * only pure-SOLID groups (any 0x10/0x11+ child colour aborts -> NEVER)
;     * first encounter only marks SEEN; bake on the second (one-off
;       cinematic shapes must not flood the arena)
;     * alloc fail -> NEVER that key only
;    (mask re-renders for mixed 0x10/0x11 groups) and 
;   (2x2 strip bakes -> the scrolling water surface) extend this file.
;
;   Colour-0-vs-transparency: cells bake colours +$10 ($10-$1F, never 0;
;   AW colour 0 is a real opaque black), empty = $00; the reuse blit strips
;   the offset with BCB_AND=$0F and BSTENCIL skips only true empty.
;
;   VRAM map (LR gameplay only; 16008 is SR and the cache resets on every
;   part switch anyway):
;     $008000-$00FFFF  arena 0 (32 KB, page-0 upper hole)
;     $018000-$019FFF  INDEX: 512 entries x 16 B (page-1 upper hole)
;     $01A000-$01FFFF  arena 1 (24 KB, rest of the page-1 upper hole)
;     $028000-$02FCFF  scratch render page (160x200, page-2 upper hole)
;   No collision with the 16008 ESC-snapshot slots ($052000/$062000/$07xxxx)
;   or the SFX banks ($0E/$0F/$11-$13/$1E/$1F).
;
;   Index entry (16 B):
;     +0 state (0 empty / 1 SEEN / 2 CELL / 3 NEVER)
;     +1 off lo  +2 off hi  +3 zoom lo  +4 zoom hi  +5 par|bank<<1
;     +6 cell addr lo  +7 mid  +8 hi
;     +9 w-1   +10 h-1
;     +11 ax (signed: cell x0 byte - 80)   +12 ay (signed: cell y0 - 100)
;     +13..15 spare class flags + sub-cell info)
;=============================================================================

        org $AA00                   ; free RAM gap (poly LUTs end $AA00, VM $B000);
                                    ;   the $2000 code region has no headroom left

CC_DIAG     equ 0                   ; bisect aid -- 0 = RELEASE (cache on)
                                    ; 1 = hits blit AND render normally
                                    ; 2 = NO blits at all (bake machinery runs,
                                    ;     everything renders normally): clean
                                    ;     screen -> blit output bug; broken ->
                                    ;     bake side effects.

CC_INDEX_V  equ $018000
CC_INDEX_BK equ $80|[CC_INDEX_V/$4000]      ; MEMAC-B bank ($86), window $4000
CC_AR0_V    equ $008000
CC_AR0_SZ   equ 32768
CC_AR1_V    equ $01A000
CC_AR1_SZ   equ 24576
CC_SCR_V    equ $028000                     ; scratch page base
CC_BAKEX    equ 160                         ; centre bake position (|x-parity)
CC_BAKEY    equ 100

; state values
CCS_EMPTY   equ 0
CCS_SEEN    equ 1
CCS_CELL    equ 2
CCS_NEVER   equ 3

; work vars ($B3D7-$B3FF free after code_return; see check_layout.py)
cc_rx    = $B3D7                   ; (2) saved real dr_x
cc_ry    = $B3D9                   ; (2) saved real dr_y
cc_key   = $B3DB                   ; (5) off lo/hi, zoom lo/hi, par|bank<<1
cc_ptr   = $80                     ; (2) index entry ptr -- ZP, needs (zp),y
                                   ;   ($80/$81 = the freed intro pl_lo/pl_mid)
cc_x0    = $B3E2                   ; bake extents (byte cols / rows)
cc_x1    = $B3E3
cc_y0    = $B3E4
cc_y1    = $B3E5
cc_flag  = $B3E6                   ; bit7 = abort (non-solid colour), bit0 = any span
cc_w     = $E9                   ; cell width (bytes)
cc_h     = $EA                   ; cell height (rows)
cc_cell  = $B3E9                   ; (3) cell VRAM address
cc_t0    = $F3                   ; (2) scratch
cc_t1    = $F5                   ; (2)
cc_dx    = $ED                   ; (2) dest x0 (signed bytes)
cc_dy    = $EF                   ; (2) dest y0 (signed rows)
cc_sk    = $F1                   ; (2) src start adjust (clip)
cc_bw    = $EB                   ; clipped blit width
cc_bh    = $EC                   ; clipped blit height
cc_bmp0  = $B3F8                   ; (2) arena 0 bump offset
cc_bmp1  = $B3FA                   ; (2) arena 1 bump offset
cc_baking = $B3FC                  ; 1 = bake render in progress (do_fill's clip
                                   ;   dispatch aborts the bake -- see aw_polygon)
cc_lst   = $B3FF                   ; cell BCB list in use: 0 / 2*BCB_SIZE (cc_blit alternates)
cc_roff  = $B3FD                   ; (2) saved dr_off across the bake render. The
                                   ;   bake's poly_draw walks the hier tree and
                                   ;   LEAVES dr_off mid-group (do_hier restores it
                                   ;   to the last child's post-header offset, not
                                   ;   the group start). A NEVER verdict then falls
                                   ;   through to do_draw's ?ddnc, which re-renders
                                   ;   via set_poly_ptr(dr_off) -> a wrong offset.
                                   ;   So dr_off MUST be saved/restored like dr_x/y.

; --- arena allocator table (5 arenas) in the $9E80 work-RAM gap -------------
;   0,1 = the two fixed page-upper holes ($008000/32K, $01A000/24K).
;   2,3,4 = this part's v1/code/v2 region remainders, set at load_part from the
;   streamed SECTOR COUNTS: base = region_base + cnt*128 (sector-aligned -> sits
;   right ABOVE the part data, so cells never overlap live data), size to the
;   region top (v2 capped at $078000, before the SFX banks $1E/$1F). The 16008
;   ESC-snapshot PSAV slots live in these regions too, but the cache is wiped on
;   every part switch (16008 is SR, no cache) so they never coexist.
;   Guarded by check_layout ("cc arena table").
ARENA_N    = 5
cc_ar_blo  = $9E80                 ; [5] cell base, 24-bit (lo/mid/hi)
cc_ar_bmid = $9E85
cc_ar_bhi  = $9E8A
cc_ar_szlo = $9E8F                 ; [5] arena size, 16-bit
cc_ar_szhi = $9E94
cc_ar_bplo = $9E99                 ; [5] bump (bytes used), 16-bit
cc_ar_bphi = $9E9E
cc_rr0     = $9EA3                 ; (3) cnt<<7 scratch (set_arena)
cc_rr1     = $9EA4
cc_rr2     = $9EA5
cc_arhi    = $9EA6                 ; (1) region base-hi temp (set_arena)

;=============================================================================
; cc_invalidate : wipe the cache (boot + every load_part). The 512x16 index
;   is cleared by one blitter fill; the bumps reset. COLD -> the $1DC0 gap.
;=============================================================================
        org $1DC0                   ; free gap: text data ends $1DB6, code at $2000
.if 1
.proc cc_invalidate
        lda #0
        sta BCB+BCB_SRC_ADDR        ; (template fields may hold anything here)
        sta BCB+BCB_DST_ADDR
        sta cc_lst                  ; cell blits start on list 0
        lda #>[CC_INDEX_V&$FFFF]
        sta BCB+BCB_DST_ADDR+1
        lda #[CC_INDEX_V>>16]
        sta BCB+BCB_DST_ADDR+2
        ; the index is 8 KB, but a blit row is at most 512 bytes (WIDTH is 9 bits,
        ; alt-src/Altirra vbxe.cpp LoadBCB: `(rawBltWidth1 & 0x01) << 8`) -- the old
        ; one-row WIDTH=$1FFF cleared only 512 B (32 of the 512 entries), so stale
        ; entries of the previous part survived. 16 rows x 512 with stride 512.
        lda #<[512-1]
        sta BCB+BCB_WIDTH
        lda #>[512-1]
        sta BCB+BCB_WIDTH+1
        lda #16-1
        sta BCB+BCB_HEIGHT
        lda #<512
        sta BCB+BCB_DST_STEPY
        sta BCB+BCB_XOR             ; (<512 = 0)
        sta BCB+BCB_AND
        lda #>512
        sta BCB+BCB_DST_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jsr fire_fill
        lda #$FF                    ; BCB mode fields + WIDTH+1 clobbered
        sta last_scol
        sta bcb_pg                  ; (the span BCB page / height shadow too)
        ; --- reset the 5-arena allocator: zero every bump + size, then set the
        ;     two fixed page-hole arenas (2/3/4 are filled later by load_part). ---
        ldx #ARENA_N-1
        lda #0
?za     sta cc_ar_bplo,x
        sta cc_ar_bphi,x
        sta cc_ar_szlo,x            ; size 0 -> arena skipped until load_part sets it
        sta cc_ar_szhi,x
        dex
        bpl ?za
        sta cc_baking               ; (A still 0)
        lda #<[CC_AR0_V&$FFFF]      ; arena 0 = $008000 / 32768
        sta cc_ar_blo+0
        lda #>[CC_AR0_V&$FFFF]
        sta cc_ar_bmid+0
        lda #[CC_AR0_V>>16]
        sta cc_ar_bhi+0
        lda #<CC_AR0_SZ
        sta cc_ar_szlo+0
        lda #>CC_AR0_SZ
        sta cc_ar_szhi+0
        lda #<[CC_AR1_V&$FFFF]      ; arena 1 = $01A000 / 24576
        sta cc_ar_blo+1
        lda #>[CC_AR1_V&$FFFF]
        sta cc_ar_bmid+1
        lda #[CC_AR1_V>>16]
        sta cc_ar_bhi+1
        lda #<CC_AR1_SZ
        sta cc_ar_szlo+1
        lda #>CC_AR1_SZ
        sta cc_ar_szhi+1
        jsr blit_idle               ; the index must be clear before any lookup
        lda #<SCRW                  ; ... and the span stride back (the engine
        sta BCB+BCB_DST_STEPY       ;   invariant DST_STEPY = 160)
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
        jmp cc_tmpl_init            ; the cell-blit BCB templates (VRAM, idle blitter)
.endp
.else
.proc cc_invalidate
        jsr blit_idle
        lda #0
        sta BCB+BCB_SRC_ADDR        ; (template fields may hold anything here)
        sta BCB+BCB_DST_ADDR
        lda #>[CC_INDEX_V&$FFFF]
        sta BCB+BCB_DST_ADDR+1
        lda #[CC_INDEX_V>>16]
        sta BCB+BCB_DST_ADDR+2
        lda #<[512*16-1]            ; one linear 8 KB row
        sta BCB+BCB_WIDTH
        lda #>[512*16-1]
        sta BCB+BCB_WIDTH+1
        lda #0
        sta BCB+BCB_HEIGHT
        sta BCB+BCB_XOR
        sta BCB+BCB_AND
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jsr fire_fill
        lda #$FF                    ; BCB mode fields clobbered
        sta last_scol
        ; --- reset the 5-arena allocator: zero every bump + size, then set the
        ;     two fixed page-hole arenas (2/3/4 are filled later by load_part). ---
        ldx #ARENA_N-1
        lda #0
?za     sta cc_ar_bplo,x
        sta cc_ar_bphi,x
        sta cc_ar_szlo,x            ; size 0 -> arena skipped until load_part sets it
        sta cc_ar_szhi,x
        dex
        bpl ?za
        sta cc_baking               ; (A still 0)
        lda #<[CC_AR0_V&$FFFF]      ; arena 0 = $008000 / 32768
        sta cc_ar_blo+0
        lda #>[CC_AR0_V&$FFFF]
        sta cc_ar_bmid+0
        lda #[CC_AR0_V>>16]
        sta cc_ar_bhi+0
        lda #<CC_AR0_SZ
        sta cc_ar_szlo+0
        lda #>CC_AR0_SZ
        sta cc_ar_szhi+0
        lda #<[CC_AR1_V&$FFFF]      ; arena 1 = $01A000 / 24576
        sta cc_ar_blo+1
        lda #>[CC_AR1_V&$FFFF]
        sta cc_ar_bmid+1
        lda #[CC_AR1_V>>16]
        sta cc_ar_bhi+1
        lda #<CC_AR1_SZ
        sta cc_ar_szlo+1
        lda #>CC_AR1_SZ
        sta cc_ar_szhi+1
        jmp blit_idle               ; the index must be clear before any lookup
        ;rts
.endp
.endif

;=============================================================================
; cc_set_arena : fill arena entry Y from a streamed region's sector count.
;   in : Y = arena index (2/3/4) ; cc_t0:cc_t0+1 = sector count
;        A = region base hi ($05/$06/$07)
;        cc_t1:cc_t1+1 = region top low-16 ($0000 = 64 KB region, $8000 = 32 KB)
;   base = (A<<16) + cnt*128 ; size = region_top - cnt*128. Cold (load_part only).
;=============================================================================
.if 1
; (skill pass 2026-09-22: cnt*128 shifted in A and stored once, straight into the
;  arena table -- the rr0..rr2 copy + the 3-byte shift in memory are gone; the region
;  tops cc_t1 are set once for the two 64 KB regions, cc_set_arena leaves them alone.)
.proc cc_set_arena
        sta cc_arhi
        lda cc_t0+1
        lsr @                       ; hi(cnt*128) = cnt_hi >> 1, C = its bit 0
        tax                         ;   (X is free: every caller reloads it)
        lda cc_t0
        ror @                       ; mid(cnt*128)
        sta cc_ar_bmid,y
        lda #0
        ror @                       ; lo(cnt*128) = bit 0 of cnt, at bit 7
        sta cc_ar_blo,y
        txa
        clc
        adc cc_arhi
        sta cc_ar_bhi,y             ; base = region + data size
        lda cc_t1                   ; size = region_top_low16 - cnt*128
        sec
        sbc cc_ar_blo,y
        sta cc_ar_szlo,y
        lda cc_t1+1
        sbc cc_ar_bmid,y
        sta cc_ar_szhi,y
        rts
.endp

.proc cc_init_arenas
        ldx dk_idx
        lda atr_v1_cnt_lo,x
        sta cc_t0
        lda atr_v1_cnt_hi,x
        sta cc_t0+1
        lda #0                      ; v1 + code regions (64 KB): top low16=$0000
        sta cc_t1
        sta cc_t1+1
        ldy #2
        lda #$05                    ; v1 region $050000-$05FFFF
        jsr cc_set_arena
        ldx dk_idx
        lda atr_code_cnt_lo,x
        sta cc_t0
        lda atr_code_cnt_hi,x
        sta cc_t0+1
        ldy #3
        lda #$06                    ; code region $060000-$06FFFF
        jsr cc_set_arena
        ldx dk_idx
        lda atr_v2_cnt_lo,x
        sta cc_t0
        lda atr_v2_cnt_hi,x
        sta cc_t0+1
        lda #$80                    ; v2 region $070000-$077FFF (32 KB; $1E/$1F = SFX):
        sta cc_t1+1                 ;   top low16 = $8000
        ldy #4
        lda #$07
        jmp cc_set_arena            ; tail-call
.endp
.else
.proc cc_set_arena
        sta cc_arhi
        lda #0
        sta cc_rr0
        lda cc_t0
        sta cc_rr1
        lda cc_t0+1
        sta cc_rr2                  ; rr2:rr1:rr0 = cnt<<8
        lsr cc_rr2
        ror cc_rr1
        ror cc_rr0                  ; >>1 -> cnt*128 (= bytes of streamed data)
        lda cc_rr0
        sta cc_ar_blo,y
        lda cc_rr1
        sta cc_ar_bmid,y
        lda cc_arhi
        clc
        adc cc_rr2
        sta cc_ar_bhi,y             ; base = region + data size
        lda cc_t1                   ; size = region_top_low16 - cnt*128
        sec
        sbc cc_rr0
        sta cc_ar_szlo,y
        lda cc_t1+1
        sbc cc_rr1
        sta cc_ar_szhi,y
        rts
.endp

;=============================================================================
; cc_init_arenas : after load_part wiped the cache, register arenas 2/3/4 from
;   THIS part's streamed v1/code/v2 sector counts (dk_idx). Cold (load-time).
;=============================================================================
.proc cc_init_arenas
        ldx dk_idx
        lda atr_v1_cnt_lo,x
        sta cc_t0
        lda atr_v1_cnt_hi,x
        sta cc_t0+1
        lda #0                      ; v1 region $050000-$05FFFF (64 KB): top low16=$0000
        sta cc_t1
        sta cc_t1+1
        ldy #2
        lda #$05
        jsr cc_set_arena
        ldx dk_idx
        lda atr_code_cnt_lo,x
        sta cc_t0
        lda atr_code_cnt_hi,x
        sta cc_t0+1
        lda #0                      ; code region $060000-$06FFFF (64 KB)
        sta cc_t1
        sta cc_t1+1
        ldy #3
        lda #$06
        jsr cc_set_arena
        ldx dk_idx
        lda atr_v2_cnt_lo,x
        sta cc_t0
        lda atr_v2_cnt_hi,x
        sta cc_t0+1
        lda #0                      ; v2 region $070000-$077FFF (32 KB; $1E/$1F = SFX)
        sta cc_t1
        lda #$80                    ;   top low16 = $8000
        sta cc_t1+1
        ldy #4
        lda #$07
        jmp cc_set_arena            ; tail-call
.endp
.endif
; op_condjmp's take/skip decision table (index = op*4 + lt*2 + eq), moved out of
; the $2000 chain: data reads work from anywhere, and the 32 bytes there pushed
; game_vm_draw's nocross4 pad over a page. Must not cross a page itself (abs,x).
.if [*&$FF]>$E0
        org [[*&$FF00]+$100]
.endif
cj_take dta 0,1,0,1, 1,0,1,0, 1,0,0,0, 1,1,0,0, 0,0,1,1, 0,1,1,1, 0,1,1,1, 0,1,1,1
        ert *>$1EFF                 ; the cold gap now ends at $1F00: vm_oplo/vm_ophi
                                    ;   (game_vm_optab.asm) own $1F00-$1F7F

        org $AA00                   ; hot routines continue in the $AA00 gap

;=============================================================================
; cc_lookup : key (dr_off/dr_zoom/dr_x parity/poly_base_adj) -> cc_ptr = the
;   entry's window address, MEMAC-B switched to the index bank. A = state
;   (CCS_EMPTY if the slot holds a different key).
;=============================================================================
.if 1
; The key and the slot are composed in A as the key bytes are loaded (slot low =
; k0^k1^k2^k3, slot bit 8 = (k1^k3^k4)&1, entry = $4000 + slot<<4): no slot cell,
; no read-modify-write of the pointer. Same slot, same key, same compare.
.proc cc_lookup
        lda dr_x
        and #1
        ldx poly_base_adj           ; 0 = video1, 8 = video2
        beq ?b1
        ora #2
?b1     sta cc_key+4                ; k4 = x parity | bank<<1
        eor dr_off+1
        eor dr_zoom+1
        and #1
        tay                         ; Y = slot bit 8
        lda dr_off
        sta cc_key+0
        lda dr_off+1
        sta cc_key+1
        lda dr_zoom+1
        sta cc_key+3
        lda dr_zoom
        sta cc_key+2
        eor dr_off
        eor dr_off+1
        eor dr_zoom+1               ; slot low 8
        tax
        asl @
        asl @
        asl @
        asl @
        sta cc_ptr                  ; (slot<<4) low
        txa
        lsr @
        lsr @
        lsr @
        lsr @
        ora ?hi,y                   ; + bit8<<12 + the window base (disjoint bits)
        sta cc_ptr+1
        lda #CC_INDEX_BK            ; switch MEMAC-B to the index bank
        sta memb_cur                ;   (memb_cur FIRST: the sound IRQ restores
        sta VBXE_MEMAC_B            ;   the register to memb_cur)
        ldy #0                      ; key compare
        lda (cc_ptr),y
        beq ?ret                    ; empty slot
        tax                         ; X = state
        ldy #5
?cmp    lda (cc_ptr),y
        cmp cc_key-1,y              ; entry +1..+5 vs cc_key+0..+4 (skill-ok TABX:
                                    ;   5 bytes, read with y = 5..1)
        bne ?miss
        dey
        bne ?cmp
        txa                         ; key match -> state
        rts
?miss   lda #CCS_EMPTY              ; different key in the slot -> treat as empty
?ret    rts
?hi     dta >DATAW, >DATAW|$10
.endp
.else
.proc cc_lookup
        lda dr_off                  ; build the key
        sta cc_key+0
        lda dr_off+1
        sta cc_key+1
        lda dr_zoom
        sta cc_key+2
        lda dr_zoom+1
        sta cc_key+3
        lda dr_x
        and #1
        sta cc_key+4
        lda poly_base_adj           ; 0 = video1, 8 = video2
        beq ?b1
        lda #2
        ora cc_key+4
        sta cc_key+4
?b1
        ; slot hash (9 bits) -> entry addr = $4000 + (slot<<4)
        ; Fold the high key bytes (off_hi, zoom_hi) into the LOW 8 slot bits.
        ; The old hash mixed them only into bit-8, so off_hi (the byte that most
        ; distinguishes distinct shapes) collapsed to a single bit -> heavy index
        ; thrash under the ~370 distinct water shapes (recurring shapes evicted
        ; before they cache). Spreading off_hi/zoom_hi across the low byte spaces
        ; them out. Output is bit-identical (the full 5-byte key compare guards a
        ; wrong slot as a clean MISS); worst case neutral, never worse.
        lda cc_key+0
        eor cc_key+2
        eor cc_key+1                ; + off_hi  (was only a bit-8 contribution)
        eor cc_key+3                ; + zoom_hi
        sta cc_t0                   ; slot low 8
        lda cc_key+1
        eor cc_key+3
        eor cc_key+4
        and #1
        sta cc_t0+1                 ; slot bit 8
        lda cc_t0
        asl @
        asl @
        asl @
        asl @
        sta cc_ptr                  ; (slot<<4) low
        lda cc_t0
        lsr @
        lsr @
        lsr @
        lsr @
        sta cc_ptr+1
        lda cc_t0+1
        beq ?nb8
        lda cc_ptr+1
        ora #$10                    ; + bit8<<12
        sta cc_ptr+1
?nb8    lda cc_ptr+1
        ora #>DATAW                 ; + window base $4000
        sta cc_ptr+1
        lda #CC_INDEX_BK            ; switch MEMAC-B to the index bank
        sta memb_cur                ;   (memb_cur FIRST: the sound IRQ restores
        sta VBXE_MEMAC_B            ;   the register to memb_cur)
        ldy #0                      ; key compare
        lda (cc_ptr),y
        beq ?ret                    ; empty slot
        tax                         ; X = state
        ldy #5
?cmp    lda (cc_ptr),y
        cmp cc_key-1,y              ; entry +1..+5 vs cc_key+0..+4
        bne ?miss
        dey
        bne ?cmp
        txa                         ; key match -> state
        rts
?miss   lda #CCS_EMPTY              ; different key in the slot -> treat as empty
        rts
?ret    rts
.endp
.endif

;=============================================================================
; cc_wrentry : write state A + the key into the entry at cc_ptr (index bank
;   must still be selected). Used for SEEN / NEVER; CELL adds fields after.
;=============================================================================
.proc cc_wrentry
        ldy #0
        sta (cc_ptr),y
        ldy #5
?k      lda cc_key-1,y              ; skill-ok TABX: 5 bytes, y = 5..1
        sta (cc_ptr),y
        dey
        bne ?k
        rts
.endp

;=============================================================================
; cc_draw : the do_draw hook. Returns C=1 when the draw was fully handled
;   (hit or bake), C=0 when the caller must run the normal poly_draw.
;=============================================================================
.proc cc_draw
        jsr cc_lookup
        cmp #CCS_CELL
        beq ?hit
        cmp #CCS_NEVER
        beq ?no
        cmp #CCS_SEEN
        beq ?bake
        lda #CCS_SEEN               ; first encounter -> SEEN, draw normally
        jsr cc_wrentry
?no     clc
        rts
?hit
.if CC_DIAG=2
        clc                         ; DIAG2: no blit, caller renders normally
.else
        jsr cc_blit                 ; cell -> page at (dr_x, dr_y), clipped
.if CC_DIAG=1
        clc                         ; DIAG1: also let the caller render normally
.else
        sec
.endif
.endif
        rts
?bake   ;jmp cc_bake                 ; returns C=1 handled / C=0 fall through
        ert *<>cc_bake              ; falls through into cc_bake
.endp

;=============================================================================
; cc_bake : render the shape once at the screen centre into the scratch page
;   (colours +$10, extents tracked, non-solid colours abort), then cut the
;   bbox rect into an arena cell, write the CELL entry, and blit it to the
;   real position. On abort/edge/nofit the key goes NEVER and C=0 lets the
;   caller draw normally.
;=============================================================================
.proc cc_bake
        lda dr_x                    ; save the real position
        sta cc_rx
        lda dr_x+1
        sta cc_rx+1
        lda dr_y
        sta cc_ry
        lda dr_y+1
        sta cc_ry+1
        lda dr_off                  ; the bake's poly_draw mangles dr_off (hier
        sta cc_roff                 ;   tree walk) -> save it so the NEVER-verdict
        lda dr_off+1                ;   re-render in do_draw.?ddnc starts from the
        sta cc_roff+1               ;   correct offset (see cc_roff note above)
        lda dr_x                    ; bake position: centre, same x parity
        and #1
        ora #CC_BAKEX
        sta dr_x
        lda #0
        sta dr_x+1
        lda #CC_BAKEY
        sta dr_y
        lda #0
        sta dr_y+1
        ; clear the scratch page (one 32000-B fill, colour 0)
        ;lda #0
        sta BCB+BCB_DST_ADDR
        lda #>[CC_SCR_V&$FFFF]
        sta BCB+BCB_DST_ADDR+1
        lda #[CC_SCR_V>>16]
        sta BCB+BCB_DST_ADDR+2
        lda #<[SCRW-1]
        sta BCB+BCB_WIDTH
        lda #>[SCRW-1]
        sta BCB+BCB_WIDTH+1
        lda #<SCRW
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
        lda #SCRH-1
        sta BCB+BCB_HEIGHT
        lda #0
        sta BCB+BCB_AND
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jsr fire_fill
        lda #$FF
        sta last_scol
        sta bcb_pg                  ; (the span BCB page / height shadow too)
        ; route polygon spans to bake_span and DOTS to bake_dot; reset state.
        ; (draw_dots skips x >= 256 on the 6502 -- POSITION-dependent, so any
        ; shape containing dots is uncacheable.)
        lda #<bake_span
        sta emit_span.cc_fsp+1
        lda #>bake_span
        sta emit_span.cc_fsp+2
        lda #<bake_dot
        sta draw_dots.cc_dds+1
        lda #>bake_dot
        sta draw_dots.cc_dds+2
.if 1
        lda #$4C                    ; the fused LR spans write the BCB themselves:
        sta draw_scanline_fast      ;   route them through cc_fsp while baking
        lda #<dsf_bake
        sta draw_scanline_fast+1
        lda #>dsf_bake
        sta draw_scanline_fast+2
        lda #<emit_span.es_lr
        sta draw_scanline.dsl_rdy+1
        lda #>emit_span.es_lr
        sta draw_scanline.dsl_rdy+2
.endif
        lda #$FF
        sta cc_x0
        sta cc_y0
        lda #0
        sta cc_x1
        sta cc_y1
        sta cc_flag
.if 1
.if 1
pbh_6   lda #0  ; SMC = poly_bcb_h; every bake span is the same height (1 or 2 rows)
.else
        lda poly_bcb_h              ; every bake span is the same height (1 or 2 rows)
.endif
        sta BCB+BCB_HEIGHT
.endif
        ; render (rs_smc was set by do_draw; poly stream pointer per shape)
        lda #1
        sta cc_baking               ; arm the do_fill clip guard
        jsr set_poly_ptr
        jsr poly_draw
        lda #0
        sta cc_baking
        lda #<fill_span             ; un-patch the span dispatch
        sta emit_span.cc_fsp+1
        sta draw_dots.cc_dds+1
        lda #>fill_span
        sta emit_span.cc_fsp+2
        sta draw_dots.cc_dds+2
        ; (cc_dds is a jsr, cc_fsp a jmp -- both 3-byte, operand at +1/+2)
.if 1
        lda #$A6                    ; the fused LR spans back: ldx hy_lo / the 3rd
        sta draw_scanline_fast      ;   byte (DSF_B2, aw_raster)
        lda #hy_lo
        sta draw_scanline_fast+1
        lda #DSF_B2
        sta draw_scanline_fast+2
        lda #<draw_scanline_fast.es_lrf
        sta draw_scanline.dsl_rdy+1
        lda #>draw_scanline_fast.es_lrf
        sta draw_scanline.dsl_rdy+2
.endif
        lda cc_rx                   ; restore the real position
        sta dr_x
        lda cc_rx+1
        sta dr_x+1
        lda cc_ry
        sta dr_y
        lda cc_ry+1
        sta dr_y+1
        lda cc_roff                 ; restore the real poly offset (mangled by the
        sta dr_off                  ;   bake's hier walk) -- a NEVER verdict re-
        lda cc_roff+1               ;   renders via ?ddnc/set_poly_ptr from dr_off
        sta dr_off+1
        ; verdict
        lda cc_flag
        bmi ?never2                 ; non-solid colour seen
        and #1
        beq ?never2                 ; empty render
        lda cc_x0                   ; extents touching any edge = the centre
        beq ?never2                 ;   render was (possibly) clipped -> NEVER
        lda cc_y0                   ;   no strips / re-placement)
        beq ?never2
        lda cc_x1
        cmp #SCRW-1
        beq ?never2
        lda cc_y1
        cmp #SCRH-1
        bne ?wok
        ;jmp ?wok
?never2 jmp ?never                  ; near trampoline (?never is far below)
?wok    lda cc_x1                   ; w = x1-x0+1 ; h = y1-y0+1
        sec
        sbc cc_x0                   ; C = 1: x1 >= x0 (the render was non-empty,
        adc #0                      ;   cc_flag bit 0 -> extents are min <= max),
        sta cc_w                    ;   so the +1 rides the known carry
        lda cc_y1
        sec
        sbc cc_y0                   ; C = 1: y1 >= y0 (same proof)
        adc #0
        sta cc_h
        jsr cc_alloc                ; -> cc_cell (C=0 nofit)
        bcc ?never2
        ; copy scratch[bbox] -> cell  (src stride 160, dst stride w)
        ldx cc_y0                   ; src = CC_SCR_V + y0*160 + x0
        lda row_lo,x                ;   row_lut is -ROWBIAS biased; x0 is a raw
        clc                         ;   byte col -> add ROWBIAS back
        adc cc_x0
        sta cc_t0
        lda row_hi,x
        adc #>ROWBIAS
        sta cc_t0+1
        lda cc_t0
        sta BCB+BCB_SRC_ADDR
        lda cc_t0+1
        clc
        adc #>[CC_SCR_V&$FFFF]
        sta BCB+BCB_SRC_ADDR+1
        lda #[CC_SCR_V>>16]
        sta BCB+BCB_SRC_ADDR+2
        lda #<SCRW
        sta BCB+BCB_SRC_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        lda cc_cell
        sta BCB+BCB_DST_ADDR
        lda cc_cell+1
        sta BCB+BCB_DST_ADDR+1
        lda cc_cell+2
        sta BCB+BCB_DST_ADDR+2
        lda cc_w
        sta BCB+BCB_DST_STEPY
        lda #0
        sta BCB+BCB_DST_STEPY+1
        ldx cc_w
        dex
        stx BCB+BCB_WIDTH
        ;lda #0
        sta BCB+BCB_WIDTH+1
        ldx cc_h
        dex
        stx BCB+BCB_HEIGHT
        lda #$FF
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
        jsr fire_fill
.if 1
.else
        ; RESTORE THE ENGINE INVARIANT DST_STEPY=160 IMMEDIATELY: the rect copy
        ; set it to the cell stride, and the post-bake cc_blit may be clipped
        ; out entirely (off-screen shape) and never reset it -- the next normal
        ; 2-tall spans then stepped rows by cell-width = the striped/doubled
        ; corruption (pasy.png).
        jsr blit_idle
        lda #<SCRW
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
        lda #$FF
        sta last_scol
        sta bcb_pg                  ; (the span BCB page / height shadow too)
.endif
        ; write the CELL entry (re-select the index bank: bake blits did not
        ; change MEMAC-B, but set_poly_ptr did)
        lda #CC_INDEX_BK
        sta memb_cur
        sta VBXE_MEMAC_B
        lda #CCS_CELL
        jsr cc_wrentry
        ldy #6
        lda cc_cell
        sta (cc_ptr),y
        iny
        lda cc_cell+1
        sta (cc_ptr),y
        iny
        lda cc_cell+2
        sta (cc_ptr),y
        iny                         ; +9 w-1
        ldx cc_w
        dex
        txa
        sta (cc_ptr),y
        iny                         ; +10 h-1
        ldx cc_h
        dex
        txa
        sta (cc_ptr),y
        iny                         ; +11 ax = x0 - 80 (signed)
        lda cc_x0
        sec
        sbc #CC_BAKEX/2
        sta (cc_ptr),y
        iny                         ; +12 ay = y0 - 100 (signed)
        lda cc_y0
        sec
        sbc #CC_BAKEY
        sta (cc_ptr),y
.if 1
        ; RESTORE THE ENGINE INVARIANT DST_STEPY=160 before anything else can START:
        ; the rect copy set it to the cell stride, and the post-bake cc_blit may be
        ; clipped out entirely (off-screen shape) -- the next 2-tall spans would step
        ; rows by the cell width (pasy.png). The copy runs on meanwhile (the fields
        ; are free once it STARTed); its result is waited for by cc_blit's START.
        lda #<SCRW
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
        lda #$FF
        sta last_scol
        sta bcb_pg                  ; (the span BCB page / height shadow too)
.endif
.if CC_DIAG=2
        clc                         ; DIAG2: entry written, but render normally
.else
        jsr cc_blit                 ; first draw: cell -> the real position
        sec
.endif
        rts
?never  lda #CC_INDEX_BK            ; the bake render switched banks (poly data)
        sta memb_cur
        sta VBXE_MEMAC_B
        lda #CCS_NEVER
        jsr cc_wrentry
        clc                         ; caller draws normally
        rts
.endp

;=============================================================================
; cc_alloc : bump-allocate cc_w*cc_h bytes -> cc_cell (24-bit). C=0 = no room
;   in either arena (the caller NEVERs the key; others may still fit later).
;=============================================================================
.proc cc_alloc
        lda cc_w                    ; size = w*h (8x8 -> 16, square tables)
        sta fmlb_l1+1               ; fmul_seta inlined (callee < its 12-cycle call)
        sta fmlb_h1+1
        eor #$FF
        sta fmlb_l2+1
        sta fmlb_h2+1
        ldx cc_h
        jsr fmul_b                  ; qp_lo:qp_hi = cell size
        ldx #0                      ; walk the 5 arenas in order
?try    lda cc_ar_bplo,x            ; new_bump = bump[x] + size
        clc
        adc qp_lo
        sta cc_t0
        lda cc_ar_bphi,x
        adc qp_hi
        sta cc_t0+1
        bcs ?next                   ; 16-bit overflow -> way past this arena
        lda cc_ar_szhi,x            ; fits if new_bump <= size[x] (unsigned 16-bit)
        cmp cc_t0+1
        bcc ?next                   ; size_hi < newbump_hi -> over
        bne ?fit                    ; size_hi > newbump_hi -> fits
        lda cc_ar_szlo,x
        cmp cc_t0
        bcc ?next                   ; size_lo < newbump_lo -> over
?fit    lda cc_ar_blo,x             ; cell = base[x] + bump[x]
        clc
        adc cc_ar_bplo,x
        sta cc_cell
        lda cc_ar_bmid,x
        adc cc_ar_bphi,x
        sta cc_cell+1
        lda cc_ar_bhi,x
        adc #0
        sta cc_cell+2
        lda cc_t0                   ; commit the new bump
        sta cc_ar_bplo,x
        lda cc_t0+1
        sta cc_ar_bphi,x
        sec
        rts
?next   inx                         ; skill-ok LOOPCP: first fit -- the arenas must be
        cpx #ARENA_N                ;   tried in order (0 = the big one)
        bne ?try
        clc                         ; no arena had room -> caller NEVERs this key
        rts
.endp

;=============================================================================
; bake_span : the fill_span stand-in during a bake. Same inputs (sx biased
;   +ROWBIAS, sy, slen = width-1 [<256 in LR], scol). Solid colours blit
;   col+$10 into the scratch page and update the extents; scol >= $10 sets
;   the abort flag dest-dependent groups are not cacheable).
;=============================================================================
.proc bake_span
        lda scol
        cmp #$10
        bcc ?solid
        lda cc_flag                 ; 0x10/0x11+ child -> abort the bake
        ora #$80
        sta cc_flag
        rts
?solid  lda cc_flag
        ora #1                      ; at least one span rendered
        sta cc_flag
        lda sx_lo                   ; byte col = sx - ROWBIAS = sx_lo (col<160)
        cmp cc_x0
        bcs ?nx0
        sta cc_x0
?nx0    lda sx_lo
        clc
        adc slen_lo                 ; x1 = col + (w-1)
        cmp cc_x1
        bcc ?nx1
        sta cc_x1
?nx1    lda sy
        cmp cc_y0
        bcs ?ny0
        sta cc_y0
?ny0    lda sy                      ; y1 candidate: half-res spans are 2 rows
        clc                         ;   tall; track the LAST row they cover
.if 1
pbh_7   adc #0  ; SMC = poly_bcb_h
.else
        adc poly_bcb_h
.endif
        cmp cc_y1
        bcc ?ny1
        sta cc_y1
?ny1
        ; blit the span into the scratch page (solid COPY, colour +$10)
        ldx sy                      ; offset = row_lut[sy] + sx (bias cancels)
        lda row_lo,x
        clc
        adc sx_lo
        sta cc_t1
        lda row_hi,x
        adc sx_hi
        sta cc_t1+1
.if 1
.else
?bw     lda VBXE_BL_BUSY            ; inlined blit_idle
        bne ?bw
.endif
        lda cc_t1
        sta BCB+BCB_DST_ADDR
        lda cc_t1+1
        clc
        adc #>[CC_SCR_V&$FFFF]      ; scratch page lives at +$8000 in bank 2
        sta BCB+BCB_DST_ADDR+1
        lda #[CC_SCR_V>>16]
        sta BCB+BCB_DST_ADDR+2
        lda slen_lo
        sta BCB+BCB_WIDTH
.if 1
        ; Per span only the destination, the width and the bake byte. The other
        ; fields are already right at every blit taken here:
        ;   HEIGHT      -- cc_bake writes it once, below (nothing else touches it
        ;                  during a bake: do_draw is not in this path);
        ;   AND / CTRL / WIDTH+1 -- fill_poly_int writes this shape's colour mode
        ;                  before its first span, and a bake span only ever blits
        ;                  for scol < $10 (solid: AND 0, CTRL BLT_COPY, WIDTH+1 0);
        ;                  the other colours abort the bake above. bake_span clears
        ;                  last_scol below, so the next shape re-writes the mode.
        lda scol
        ora #$F0                    ; bake byte = colour|$F0, never 0 (the VBXE
        sta BCB+BCB_XOR             ;   stencil tests AND writes the post-AND/XOR
                                    ;   value, so colour 0 needs the AND+OR pair at
                                    ;   reuse -- see cc_blit)
.else
        lda #0
        sta BCB+BCB_WIDTH+1
        lda poly_bcb_h              ; same 1/2-tall spans as the normal render
        sta BCB+BCB_HEIGHT
        lda #0
        sta BCB+BCB_AND
        lda scol
        ora #$F0
        sta BCB+BCB_XOR
        lda #BLT_COPY
        sta BCB+BCB_CTRL
.endif
.if 1
        lda #$FF                    ; the mode fields are the bake's now (fill_poly_int
        sta last_scol               ;   writes a shape's mode up front, bakes included)
?bw     lda VBXE_BL_BUSY            ; fire early, wait late: the fields went in while the
        ora VBXE_BL_BUSY            ;   previous blit may run (the blitter reads a BCB
        bne ?bw                     ;   only at START); hardened, right before the START
.endif
        lda #1
        sta VBXE_BL_START
        rts
.endp

;=============================================================================
; bake_dot : draw_dots stand-in during a bake. The 6502 dot plot skips x >= 256
;   (pts_xhi != 0), so dot content is POSITION-dependent -> the whole shape is
;   uncacheable. Mark the abort flag; the pixel is irrelevant (the caller falls
;   back to a full normal render).
;=============================================================================
.proc bake_dot
        lda cc_flag
        ora #$80
        sta cc_flag
        rts
.endp

;=============================================================================
; cc_blit : blit the CELL entry at cc_ptr to (dr_x, dr_y) on the current draw
;   page, clipped to the 160x200 page. Cell fields are read from the entry
;   (the index bank is still selected on entry to this routine).
;=============================================================================
.if 1
; skill pass 2026-09-22 (vbxe-blitter: "chain small follow-up blits into the same list",
;   "fire early, wait late", "a pre-filled template per slot"): the STENCIL + XOR pair is
;   ONE list of two chained BCBs in its own slot (BCBC, two lists used alternately so
;   the CPU fills one while the blitter may still run the other). A hit writes only the
;   geometry, waits (hardened: a chain) only right before its START, and returns with
;   the pair still running -- the old code waited for blit 1, then for blit 2, then
;   restored SRC_STEPY in the span BCB (which this no longer touches, so last_scol
;   stays valid). The next START anywhere goes through blit_idle (hardened).
;   6502 side: w/h stored once for both uses, dx = ax + (dr_x asr 1) in A (subtracting
;   the parity bit before the shift is a no-op), sign extension by ora #$7F / bmi.
        nocross cc_blit, cc_blit.cb_xl, cc_blit.cb_yt
.proc cc_blit
        ; geometry from the entry
        ldy #9
        lda (cc_ptr),y              ; w-1 (< 160)
        clc
        adc #1                      ; C = 0 (w <= 160)
        sta cc_w
        sta cc_bw
        iny
        lda (cc_ptr),y              ; h-1 (< 200)
        adc #1                      ; (C = 0 from above)
        sta cc_h
        sta cc_bh
        iny                         ; +11 ax (signed 8)
        lda (cc_ptr),y
        sta cc_t0
        ora #$7F                    ; sign-extend: $FF when ax < 0, else 0
        bmi ?sx1
        lda #0
?sx1    sta cc_t0+1
        iny                         ; +12 ay (signed 8)
        lda (cc_ptr),y
        sta cc_t1
        ora #$7F
        bmi ?sy1
        lda #0
?sy1    sta cc_t1+1
        ; dest x0 (bytes) = ax + (dr_x - par)>>1 = ax + (dr_x asr 1)   (signed)
        lda dr_x+1
        cmp #$80
        ror @                       ; hi asr 1, C = bit 0 of hi
        tax
        lda dr_x
        ror @                       ; lo >> 1
        clc
        adc cc_t0
        sta cc_dx
        txa
        adc cc_t0+1
        sta cc_dx+1
        ; dest y0 = ay + dr_y (signed)
        lda dr_y
        clc
        adc cc_t1
        sta cc_dy
        lda dr_y+1
        adc cc_t1+1
        sta cc_dy+1
        ; clip -> cc_sk (src skip), cc_bw/cc_bh, clamp cc_dx/cc_dy
        lda #0
        sta cc_sk
        sta cc_sk+1
        ; X: left
        lda cc_dx+1
cb_xl   bpl ?xr                     ; >= 0
        lda #0                      ; skipx = -dx
        sec
        sbc cc_dx
        cmp cc_bw
        jcs ?out                    ; fully left of the page
        sta cc_sk                   ; src skip (bytes)
        eor #$FF                    ; bw -= skipx  (~skipx + bw + 1)
        sec
        adc cc_bw
        sta cc_bw
        lda #0
        sta cc_dx
        sta cc_dx+1
?xr     ; X: right (dx >= 0 here; dx+bw <= 160 ?)
        lda cc_dx+1
        jne ?out                    ; dx >= 256 -> off the page
        lda cc_dx
        cmp #SCRW
        jcs ?out                    ; (not taken -> C = 0 is proven: no clc needed)
        adc cc_bw
        bcs ?xcl                    ; > 255 -> clip
cb_x0   cmp #SCRW+1
        bcc cb_yt
?xcl    lda #SCRW                   ; bw = 160 - dx
        sec
        sbc cc_dx
        sta cc_bw
cb_yt   ; Y: top
        lda cc_dy+1
        bpl ?yb
        lda #0
        sec
        sbc cc_dy
        cmp cc_bh
        jcs ?out
        tax                         ; skipy (the multiplier index)
        eor #$FF                    ; bh -= skipy
        sec
        adc cc_bh
        sta cc_bh
        ; src skip += skipy * w
        lda cc_w
        sta fmlb_l1+1               ; fmul_seta inlined (callee < its 12-cycle call)
        sta fmlb_h1+1
        eor #$FF
        sta fmlb_l2+1
        sta fmlb_h2+1
        jsr fmul_b                  ; (X = skipy)
        lda cc_sk
        clc
        adc qp_lo
        sta cc_sk
        lda cc_sk+1
        adc qp_hi
        sta cc_sk+1
        lda #0
        sta cc_dy
        sta cc_dy+1
?yb     ; Y: bottom
        lda cc_dy+1
        jne ?out
        lda cc_dy
        cmp #SCRH
        jcs ?out                    ; (not taken -> C = 0 is proven: no clc needed)
        adc cc_bh
        bcs ?ycl
        cmp #SCRH+1
        bcc ?go
?ycl    lda #SCRH
        sec
        sbc cc_dy
        sta cc_bh
?go     ; fill the free cell list (X = 0 / 2*BCB_SIZE): both BCBs get the geometry
        lda cc_lst
        eor #2*BCB_SIZE
        sta cc_lst
        tax
        ldy #6                      ; src = cell + cc_sk
        lda (cc_ptr),y
        clc
        adc cc_sk
        sta BCBC+BCB_SRC_ADDR,x
        sta BCBC+BCB_SIZE+BCB_SRC_ADDR,x
        iny
        lda (cc_ptr),y
        adc cc_sk+1
        sta BCBC+BCB_SRC_ADDR+1,x
        sta BCBC+BCB_SIZE+BCB_SRC_ADDR+1,x
        iny
        lda (cc_ptr),y
        adc #0
        sta BCBC+BCB_SRC_ADDR+2,x
        sta BCBC+BCB_SIZE+BCB_SRC_ADDR+2,x
        lda cc_w                    ; src stride = cell width
        sta BCBC+BCB_SRC_STEPY,x
        sta BCBC+BCB_SIZE+BCB_SRC_STEPY,x
        ldy cc_dy                   ; page offset = row_lut[dy] + dx + ROWBIAS
        lda row_lo,y
        clc
        adc cc_dx
        sta BCBC+BCB_DST_ADDR,x
        sta BCBC+BCB_SIZE+BCB_DST_ADDR,x
        lda row_hi,y
        adc #>ROWBIAS
        sta BCBC+BCB_DST_ADDR+1,x
        sta BCBC+BCB_SIZE+BCB_DST_ADDR+1,x
        lda cbase+2                 ; current draw page
        sta BCBC+BCB_DST_ADDR+2,x
        sta BCBC+BCB_SIZE+BCB_DST_ADDR+2,x
        ldy cc_bw
        dey
        tya
        sta BCBC+BCB_WIDTH,x
        sta BCBC+BCB_SIZE+BCB_WIDTH,x
        ldy cc_bh
        dey
        tya
        sta BCBC+BCB_HEIGHT,x
        sta BCBC+BCB_SIZE+BCB_HEIGHT,x
        txa                         ; BL_ADR0 of this list (same $0401xx page)
        clc
        adc #<[CTRL+BCBC_OFF]
        tay
?w      lda VBXE_BL_BUSY            ; ONLY NOW wait (hardened: the other list may be a
        bne ?w                      ;   chain between its two BCBs)
        lda VBXE_BL_BUSY
        bne ?w
        sty VBXE_BL_ADR0
        lda #1
        sta VBXE_BL_START           ; the pair runs on its own; nothing waits for it here
        lda #<BCBF_V                ; the blitter latched the list address: point BL_ADR
        sta VBXE_BL_ADR0            ;   back at the span BCB
?out    rts
.endp
        ert >[CTRL+BCBC_OFF+4*BCB_SIZE-1]<>>[CTRL+BCBC_OFF]

; cc_tmpl_init : the constant fields of both cell lists (the geometry cc_blit writes
;   per hit is left as is). BCB 1 = byte STENCIL of colour|$F0 masked to $F0, chained
;   (BLT_NEXT) to BCB 2 = XOR of the raw cell byte: $F0 ^ (colour|$F0) = colour. Both
;   skip source bytes that are 0 (the empty cell pixels). Called with the blitter idle.
.proc cc_tmpl_init
        ldx #BCB_SIZE-1
?t      lda ?tmpl,x
        sta BCBC,x
        sta BCBC+2*BCB_SIZE,x
        sta BCBC+BCB_SIZE,x
        sta BCBC+3*BCB_SIZE,x
        dex
        bpl ?t
        lda #$FF                    ; BCB 2 : AND $FF, XOR mode, end of list
        sta BCBC+BCB_SIZE+BCB_AND
        sta BCBC+3*BCB_SIZE+BCB_AND
        lda #BLT_XOR
        sta BCBC+BCB_SIZE+BCB_CTRL
        sta BCBC+3*BCB_SIZE+BCB_CTRL
        rts
?tmpl   dta $00,$00,$00, a(0), $01, $00,$00,$00, a(SCRW), $01, a(0), $00
        dta $F0, $00, $00, $00, $00, BLT_BSTENCIL|BLT_NEXT
.endp
.else
.proc cc_blit
        ; geometry from the entry
        ldy #9
        lda (cc_ptr),y              ; w-1
        clc
        adc #1
        sta cc_w
        iny
        lda (cc_ptr),y              ; h-1
        clc
        adc #1
        sta cc_h
        iny                         ; +11 ax (signed 8)
        lda (cc_ptr),y
        sta cc_t0
        and #$80                    ; sign-extend
        beq ?sx1
        lda #$FF
        dta $2C                     ; BIT abs: skip the lda #0
?sx1    lda #0
        sta cc_t0+1
        iny                         ; +12 ay (signed 8)
        lda (cc_ptr),y
        sta cc_t1
        and #$80
        beq ?sy1
        lda #$FF
        dta $2C
?sy1    lda #0
        sta cc_t1+1
        ; dest x0 (bytes) = ax + (dr_x - par)>>1   (signed)
        lda dr_x
        and #1
        sta cc_dx                   ; par (reuse cc_dx as temp)
        lda dr_x
        sec
        sbc cc_dx
        sta cc_dx
        lda dr_x+1
        sbc #0
        sta cc_dx+1
        cmp #$80                    ; arithmetic >>1
        ror cc_dx+1
        ror cc_dx
        lda cc_dx
        clc
        adc cc_t0
        sta cc_dx
        lda cc_dx+1
        adc cc_t0+1
        sta cc_dx+1
        ; dest y0 = ay + dr_y (signed)
        lda dr_y
        clc
        adc cc_t1
        sta cc_dy
        lda dr_y+1
        adc cc_t1+1
        sta cc_dy+1
        ; clip -> cc_sk (src skip), cc_bw/cc_bh, clamp cc_dx/cc_dy
        lda #0
        sta cc_sk
        sta cc_sk+1
        lda cc_w
        sta cc_bw
        lda cc_h
        sta cc_bh
        ; X: left
        lda cc_dx+1
        bpl ?xr                     ; >= 0
        lda #0                      ; skipx = -dx
        sec
        sbc cc_dx
        cmp cc_bw
        bcs ?out1                   ; fully left of the page
        sta cc_sk                   ; src skip (bytes)
        lda cc_bw
        sec
        sbc cc_sk
        sta cc_bw
        lda #0
        sta cc_dx
        sta cc_dx+1
?xr     ; X: right (dx >= 0 here; dx+bw <= 160 ?)
        lda cc_dx+1
        bne ?out1                   ; dx >= 256 -> off the page
        lda cc_dx
        cmp #SCRW
        bcs ?out1
        clc
        adc cc_bw
        bcs ?xcl                    ; > 255 -> clip
        cmp #SCRW+1
        bcc ?yt
?xcl    lda #SCRW                   ; bw = 160 - dx
        sec
        sbc cc_dx
        sta cc_bw
        jmp ?yt
?out1   jmp ?out                    ; near trampoline (?out is far)
?yt     ; Y: top
        lda cc_dy+1
        bpl ?yb
        lda #0
        sec
        sbc cc_dy
        cmp cc_bh
        bcs ?out2
        sta cc_t0                   ; skipy
        lda cc_bh
        sec
        sbc cc_t0
        sta cc_bh
        ; src skip += skipy * w
        lda cc_w
        jsr fmul_seta
        ldx cc_t0
        jsr fmul_b
        lda cc_sk
        clc
        adc qp_lo
        sta cc_sk
        lda cc_sk+1
        adc qp_hi
        sta cc_sk+1
        lda #0
        sta cc_dy
        sta cc_dy+1
?yb     ; Y: bottom
        lda cc_dy+1
        bne ?out2
        lda cc_dy
        cmp #SCRH
        bcs ?out2
        clc
        adc cc_bh
        bcs ?ycl
        cmp #SCRH+1
        bcc ?go
?ycl    lda #SCRH
        sec
        sbc cc_dy
        sta cc_bh
        jmp ?go
?out2   jmp ?out                    ; near trampoline
?go     ; src = cell + cc_sk ; dst = page + dy*160 + dx
        ldy #6
        lda (cc_ptr),y
        clc
        adc cc_sk
        sta cc_t0
        iny
        lda (cc_ptr),y
        adc cc_sk+1
        sta cc_t0+1
        iny
        lda (cc_ptr),y
        adc #0
        sta cc_t1                   ; src hi
        jsr blit_idle               ; now edit the BCB
        lda cc_t0
        sta BCB+BCB_SRC_ADDR
        lda cc_t0+1
        sta BCB+BCB_SRC_ADDR+1
        lda cc_t1
        sta BCB+BCB_SRC_ADDR+2
        lda cc_w
        sta BCB+BCB_SRC_STEPY
        lda #0
        sta BCB+BCB_SRC_STEPY+1
        lda #1
        sta BCB+BCB_SRC_STEPX
        ldx cc_dy
        lda row_lo,x                ; page offset = row_lut[dy] + dx + ROWBIAS
        clc
        adc cc_dx
        sta cc_t0
        lda row_hi,x
        adc #>ROWBIAS
        sta cc_t0+1
        lda cc_t0
        sta BCB+BCB_DST_ADDR
        lda cc_t0+1
        sta BCB+BCB_DST_ADDR+1
        lda cbase+2                 ; current draw page
        sta BCB+BCB_DST_ADDR+2
        lda #<SCRW
        sta BCB+BCB_DST_STEPY
        lda #>SCRW
        sta BCB+BCB_DST_STEPY+1
        ldx cc_bw
        dex
        stx BCB+BCB_WIDTH
        lda #0
        sta BCB+BCB_WIDTH+1
        ldx cc_bh
        dex
        stx BCB+BCB_HEIGHT
        ; --- the STENCIL+XOR blit pair (both skip processed-source == 0, so
        ; empty cell bytes never touch the page). The blitter stencil tests
        ; (and writes) the POST-AND/XOR value, so colour 0 cannot be written
        ; directly; and BLT_AND writes 0 even for source 0 (Altirra vbxe.cpp:
        ; mode 4 has no transparency -- caused black boxes). Instead, with
        ; cell bytes = colour|$F0 (never 0):
        ;   blit 1, BSTENCIL, AND=$F0: shape bytes -> dest = $F0; empty skips.
        ;   blit 2, BLT_XOR,  AND=$FF: c = colour|$F0 (nonzero for ALL colours
        ;           incl. 0) -> dest = $F0 ^ (colour|$F0) = colour (the $F0 and
        ;           colour bits are disjoint); empty c=0 skips.
        lda #$F0
        sta BCB+BCB_AND
        lda #0
        sta BCB+BCB_XOR
        lda #BLT_BSTENCIL
        sta BCB+BCB_CTRL
        jsr fire_fill
        jsr blit_idle               ; same geometry -> patch only AND/CTRL
        lda #$FF
        sta BCB+BCB_AND
        lda #BLT_XOR
        sta BCB+BCB_CTRL
        jsr fire_fill
        jsr blit_idle               ; restore the fields the span path assumes
        lda #<SCRW                  ;   constant (SRC_STEPY; copy-mode spans)
        sta BCB+BCB_SRC_STEPY
        lda #>SCRW
        sta BCB+BCB_SRC_STEPY+1
        lda #$FF                    ; mode fields clobbered -> re-patch next span
        sta last_scol
?out    rts
.endp
.endif

        ert *>$B000                 ; the $AA00 gap ends at the VM state block
