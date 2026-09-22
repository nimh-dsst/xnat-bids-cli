# Using Excel

This page uses Microsoft Excel as the example, but the same general steps apply to other spreadsheet editors (e.g. LibreOffice Calc, Google Sheets, Numbers).

Several `xnatbidscli` commands read or write TSV/CSV files (e.g. `mriconvert_qc.tsv`, `bidsmap.tsv`, query output CSVs). It's often easier to filter, sort, and edit these files in a spreadsheet editor than to hand-edit plain TSV/CSV text, especially for wide files with many columns or rows.

## TSV with Excel

### Importing TSV into Excel

1. Open Excel and go to **File > Open** (or **Data > Get Data > From Text/CSV**).
2. Select the `.tsv` file.
3. If prompted, set the delimiter to **Tab**.
4. Confirm the import; each column should populate correctly.

### Exporting TSV from Excel

1. Go to **File > Save As**.
2. Choose **Text (Tab delimited) (\*.txt)** as the file type.
3. Save, then rename the resulting `.txt` file's extension to `.tsv`.

## CSV with Excel

### Importing CSV into Excel

1. Open Excel and go to **File > Open** (or **Data > Get Data > From Text/CSV**).
2. Select the `.csv` file.
3. If prompted, set the delimiter to **Comma**.
4. Confirm the import; each column should populate correctly.

### Exporting CSV from Excel

1. Go to **File > Save As**.
2. Choose **CSV (Comma delimited) (\*.csv)** as the file type.
3. Save.

## Why use a spreadsheet editor?

Filtering, sorting, and editing values (e.g. filling in QC or bidsmap columns) is generally faster and less error-prone in a spreadsheet editor's grid view than editing the raw TSV/CSV text directly. Just make sure to re-export back to the original delimiter (tab or comma) before feeding the file back into `xnatbidscli`.
