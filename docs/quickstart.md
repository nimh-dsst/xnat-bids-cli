# Quickstart Examples

This file describes examples of common xnatcli usage, and in which order, to get you started quickly.

## 0. Installation

The following requires git and uv are installed.

```shell
git clone https://github.com/nimh-dsst/xnat-bids-cli.git
cd xnat-bids-cli
uv sync
```

## 1. Logging into XNAT

```shell
> uv run xnatcli login
XNAT server URL: https://xnat-server.domain.edu
Username: myusername
Password: 
Credentials verified and saved to ~/.xnatcli/credentials.cfg
```

## 2. Querying a project

```shell
> uv run xnatcli query PROJECT_ID -o ~/data/xnat
Wrote 100 row(s) to ~/data/xnat/PROJECT-PROJECT_ID.csv
```

## 3. Downloading a project

```shell
uv run xnatcli download -c ~/data/xnat/PROJECT-PROJECT_ID.csv -o ~/data/xnat -n 8 -l
```

This downloads all the experiments listed in the CSV file `PROJECT-PROJECT_ID.csv` to the directory `~/data/xnat`. The `-n 8` option specifies that 8 parallel downloads should be used, and the `-l` option indicates that the process should save logs.

## 4. Creating a "starter" Dcm2Bids configuration JSON file

```shell
uv run xnatcli mriconfig -i ~/data/xnat -p PROJECT_ID -o ~/data/unmapped -n 4 -l
```

This command generates a starter Dcm2Bids configuration JSON file based on the MRI data downloaded from the `~/data/xnat` directory. The `-i` option specifies the input directory containing the downloaded data, `-p` specifies the project ID (it should be within that input directory), `-o` specifies the output directory for the configuration file, `-n 4` indicates that 4 parallel processes should be used to run dcm2bids_helper, and `-l` option indicates that the process should save logs.

## 5. Running Dcm2Bids with `mriconvert`

This sort of step should be performed after the Dcm2Bids configuration JSON file has been generated and modified to match the specific needs of the project.

```shell
uv run xnatcli mriconvert -i ~/data/xnat -p PROJECT_ID -o ~/data/unmapped -y ~/data/physio -c ~/data/unmapped/PROJECT-PROJECT_ID_mriconfig/dcm2bids_config.json -n 4 -l
```

This command runs Dcm2Bids on the MRI data downloaded from the `~/data/xnat` directory. The `-i` option specifies the input directory containing the downloaded data, `-p` specifies the project ID (it should be within that input directory), `-o` specifies the output directory for the BIDS-formatted (f)MRI data, `-c` specifies the path to the Dcm2Bids configuration JSON file generated and modified from the previous step, `-y` specifies the path to a directory containing physiological data (if any), `-n 4` indicates that 4 parallel processes should be used to run dcm2bids on XNAT downloaded experiments, and `-l` option indicates that the process should save logs.

## 6. Converting physiological data using `phys2bids`

This step should be performed after the `mriconvert` step has been completed and the BIDS-formatted (f)MRI data has been generated and, most importantly, the physio file basenames have been added to `mriscans.tsv` file's `physio` column rows. Only the physio files that have been added to the `mriscans.tsv` file (and are present in the `PhysioParent` directory) will be converted to BIDS format.

```shell
uv run xnatcli physioconvert -o ~/data/unmapped -p PROJECT_ID -n 10 -l
```

## 7. Preparing to map converted BIDS data to renamed/ready-to-share data

```shell
uv run xnatcli bidsmap -i ~/data/unmapped -p PROJECT_ID
```

Writes a file for mapping all participants and sessions to new renamed/anonymized/sanitized file and folder names to `~/data/unmapped/PROJECT-PROJECT_ID_bidsmap.tsv`. Fill in the blanks and you're ready to actually map the files.

## 8. Mapping BIDS data from converted to renamed or ready-to-share data

```shell
uv run xnatcli bidsmap -i ~/data/unmapped -p PROJECT_ID -o ~/data/mapped
```

Uses the `~/data/unmapped/PROJECT-PROJECT_ID_bidsmap.tsv` file to map all participants and sessions to new renamed/anonymized/sanitized file and folder names into the `~/data/mapped` directory.
