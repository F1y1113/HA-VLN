#!/bin/bash

# Set the dataset download directory
DATASET_DIR="Data"
mkdir -p $DATASET_DIR

download_and_extract() {
    local url=$1
    local output_file="$DATASET_DIR/$2"
    local extract_dir="$DATASET_DIR/$3"

    if [ -f "$output_file" ]; then
        echo "File already exists: $output_file, skipping download."
    else
        echo "Downloading $output_file ..."
        wget -c -O "$output_file" "$url"
    fi

    echo "Extracting $output_file ..."
    unzip -q -o "$output_file" -d "$extract_dir"

    rm -f "$output_file"
    echo "Deleted: $output_file after extraction."
}

# Download HAPS 2.0 dataset
download_and_extract "https://www.dropbox.com/scl/fo/gzcypr68u881og71mvi1f/AKlI9hNHFWGVtgAIONKskgs?rlkey=489p8tcitj5bdlppooobwomik&st=mbn0km5n&dl=1" \
                     "HAPS2_0.zip" "HAPS2_0"

mv Data/HAPS2_0/human_motion_glbs_v3/* Data/HAPS2_0/
rmdir Data/HAPS2_0/human_motion_glbs_v3

# Download HA-R2R-CE dataset
download_and_extract "https://www.dropbox.com/scl/fo/v68xl2hyt8nrosw8movot/ABE35iCkVU5j6YIZW2utY1w?rlkey=05bw6r6v4xw9bxq4r5mz4zc7p&st=x25cu3bu&dl=1" \
                     "HAR2R-CE.zip" "HA-R2R"

echo "🎉 All datasets have been ready!"