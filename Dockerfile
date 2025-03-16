FROM ubuntu:22.04
# Set non-interactive mode to avoid prompting during package installation
ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update -y && apt-get upgrade -y 
RUN apt-get install -y \
    python3 \
    python3-pip\
    musescore \
    xvfb \
    sox \
    dos2unix && \
    apt-get clean

RUN apt install -y ffmpeg    

# Install Python dependencies
RUN pip3 install --upgrade pip
RUN pip install torch==2.5.0 torchaudio==2.5.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip3 install numpy
COPY . /var/task/
RUN pip install --no-cache-dir -r /var/task/requirements.txt

WORKDIR /var/task

# Give perm to RIE and entry script
ADD ./aws-lambda-rie /usr/local/bin/aws-lambda-rie
RUN chmod +x /usr/local/bin/aws-lambda-rie

COPY entry_script.sh /entry_script.sh
RUN dos2unix /entry_script.sh 
RUN chmod +x /entry_script.sh

ENTRYPOINT ["/entry_script.sh"]

# Set the command for Lambda to use
CMD ["inference.lambda_handler"]