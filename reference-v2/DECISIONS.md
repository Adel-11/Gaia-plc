# Approved v2 decisions

These decisions record Adel's explicit answers in this chat and supersede
conflicting proposals in the supplied prompt/specification.

- Start from the new pack, not a merge of the v1 engineering data. Preserve
  existing users and archive the full v1 database and project state first.
- `latest_codesys.xml` is the latest CODESYS snapshot. It wraps its objects in an
  Application resource. Read that wrapper for import only; generated exports
  must contain free objects and never an Application or device configuration.
- New RTU mappings use instance-based names, e.g. `DIP1500_2_Coils`. Preserve
  existing mapped names until a rename is explicitly requested and report
  required manual remapping in CODESYS.
- Variable declarations are edited as structured data. Their ST view is read
  only; bidirectional declaration parsing is no longer required.
- A POU body has a protected generated section and a custom section. Preserve
  custom code verbatim during regeneration. Do not rewrite or infer its logic.
  The final code still passes export consistency checks; compilation in CODESYS
  remains the acceptance test. The exact section execution/return placement is
  part of the generation stage, not the data-import stage.
- Exchange functions are authored graphically, including typed conditions and
  an optional Else branch. With no Else, a false condition leaves the destination
  unchanged. The graph-generated ST is read only. Custom sections remain separate
  from the graph and must never be overwritten by graph regeneration.
- PA "Changed from supervision" is information about the current FB behavior;
  toggling it does not automatically rewrite the FB or change running PLC behavior.
- Excel imports support merge and replace-within-uploaded-workbook modes, both
  with a preview and explicit application. Replace does not replace the entire
  project. An invalid import never changes the current project.
- Keep addresses beyond declared TCP array bounds in the design model, but
  block invalid PLCopen exports. Explain the required generator changes and
  manual CODESYS server-size/device mapping changes instead of resizing silently.
- Object view: PA/COM/EXC/IO left of the FB; INTERN and related communication/
  exchange elements to the right. UPI has only COM/EXC/INT.
- Deploy and validate one delivery stage at a time. PLCopen generation, ST
  editors and graphical authoring are not part of stage 1 and must not be
  presented as finished until their later delivery stages pass acceptance.
