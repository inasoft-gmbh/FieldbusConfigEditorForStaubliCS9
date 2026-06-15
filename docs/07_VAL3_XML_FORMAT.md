# 07 — Format der J207J208.xml (Val3-Prozessabbild)

SyCon-Export, der die benannten Signale für das Stäubli/Val3-Programm bereitstellt.
Validiert: erzeugtes Gerüst byte-gleich zur SyCon-Flach-120-Datei.

## Aufbau
```
<ProcessData xmlns="x-schema:ProcessDataSchema.xml">
  <Version major="1" minor="1"/>
  <Adapter systemTag="30fa4a5a-…" displayName="J207J208 RE/PLS[…]"
           byteOrder="little" deviceAccessPath="{…}\192.0.2.x:50111\rtcifx0_Ch0"
           stationAddress="Addr 8">          <-- Node als "Addr N"
    <Property id="6003" .../>                <-- Gerätename (base64)
    <Channel systemTag="DDAC3283-…" displayName="CIFX RE/PLS"/>
    <Module systemTag="BC47738C-…" displayName="120 Bytes In"
            moduleType="120 Bytes In" moduleAddress="1">
      <Property id="6102" type="19" value="AAAAAA=="/>     <-- In: 6102
      <Signal systemTag="…" displayName="In_Byte_0" signalType="output"
              signalAccessPath="0.0" dataType="bit" opc="1" arrayElements="8">
        <Property id="6103" type="19" value="AAAAAA=="/>   <-- Bit-Offset (LE u32 base64)
      </Signal>
      … weitere Signale …
    </Module>
    <Module … moduleAddress="2">
      <Property id="6101" …/>               <-- Out: 6101 (nicht 6102!)
      … Out-Signale (signalType="input") …
    </Module>
    <Status systemTag="8265B33B-…" displayName="Status">
      … 8 Diagnose-Signale (unsigned16/32, fixe Offsets) …
    </Status>
  </Adapter>
</ProcessData>
```

## Wichtige Details
- Signal-Attributreihenfolge: `… dataType opc arrayElements` (anders als in
  SYCON_net.xml, wo `arrayElements` vor `opc` steht).
- Signal hat hier KEIN Property 6100 (nur 6103). Einrückung mit Tabs (Signal 3 Tabs,
  Property 4 Tabs).
- `signalAccessPath`: bit = "byte.0", word/real = "byte".
- In-Modul nutzt Property 6102, Out-Modul Property 6101.
- `stationAddress="Addr N"` = NODE_ID. `deviceAccessPath` = SyCon-Verbindungspfad
  (roboterspezifisch). Modul-systemTags = wie in SYCON_net.xml-Topologie.
- systemTags der Signale MÜSSEN zu robot 1s SYCON_net.xml passen (Konsistenz/iomap).
  Die ersten 104 Bytes haben in allen J207-Projekten dieselben Signal-GUIDs.

## Generator
`gen_val3_xml.py` — Vorlage Roboter 2 (Format/Struktur), Kopfwerte auf robot 1 gepatcht
(Node 8, deviceAccessPath, Modul-systemTags), Signaldaten aus robot 1 SYCON_net.xml.
