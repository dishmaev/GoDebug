#!/bin/bash -x

# Macros
CND_CONF="${1}"
OUTPUT_BASENAME="${2}"

readonly CONST_PACKAGE_SPEC=package-spec.cfg
readonly CONST_FIELD_SPEC_VERSION=CONST_PACKAGE_VERSION

TOP=`pwd`
CND_DLIB_EXT=so
NBTMPDIR=${CND_CONF}/tmp-packaging
OUTPUT_PATH=${CND_CONF}/${OUTPUT_BASENAME}
PACKAGE_TOP_DIR=/usr/
VAR_FIELD_SPEC_VERSION=`cat $CONST_PACKAGE_SPEC | grep $CONST_FIELD_SPEC_VERSION | cut -d ' ' -f 2`

# Functions
function checkPackageSpec
{
  if [ ! -r ${CONST_PACKAGE_SPEC} ]
  then
    exit 1
  fi
}

function checkReturnCode
{
    rc=$?
    if [ $rc != 0 ]
    then
        exit $rc
    fi
}
function makeDirectory
# $1 directory path
# $2 permission (optional)
{
    mkdir -p "$1"
    checkReturnCode
    if [ "$2" != "" ]
    then
      chmod $2 "$1"
      checkReturnCode
    fi
}
function copyFileToTmpDir
# $1 from-file path
# $2 to-file path
# $3 permission
{
    cp "$1" "$2"
    checkReturnCode
    if [ "$3" != "" ]
    then
        chmod $3 "$2"
        checkReturnCode
    fi
}

# Setup
cd "${TOP}"
checkPackageSpec
mkdir -p ${CND_CONF}/package
rm -rf ${NBTMPDIR}
mkdir -p ${NBTMPDIR}

# Copy files and create directories and links
cd "${TOP}"
makeDirectory "${NBTMPDIR}/usr/bin"
copyFileToTmpDir "${OUTPUT_PATH}" "${NBTMPDIR}${PACKAGE_TOP_DIR}bin/${OUTPUT_BASENAME}" 0755


# Ensure proper rpm build environment
RPMMACROS=~/.rpmmacros
NBTOPDIR=/tmp/cnd/rpms

if [ ! -f ${RPMMACROS} ]
then
    touch ${RPMMACROS}
fi

TOPDIR=`grep _topdir ${RPMMACROS}`
if [ "$TOPDIR" == "" ]
then
    echo "**********************************************************************************************************"
    echo Warning: rpm build environment updated:
    echo \"%_topdir ${NBTOPDIR}\" added to ${RPMMACROS}
    echo "**********************************************************************************************************"
    echo %_topdir ${NBTOPDIR} >> ${RPMMACROS}
fi
mkdir -p ${NBTOPDIR}/RPMS

# Create spec file
cd "${TOP}"
SPEC_FILE=${NBTMPDIR}/../${OUTPUT_BASENAME}.spec
rm -f ${SPEC_FILE}

cd "${TOP}"
echo BuildRoot: ${TOP}/${NBTMPDIR} >> ${SPEC_FILE}
echo 'Summary: Sample application' >> ${SPEC_FILE}
echo "Name: ${OUTPUT_BASENAME}" >> ${SPEC_FILE}
echo "Version: ${VAR_FIELD_SPEC_VERSION}" >> ${SPEC_FILE}
echo 'Release: 1' >> ${SPEC_FILE}
echo 'Group: Applications/System' >> ${SPEC_FILE}
echo 'License: MIT' >> ${SPEC_FILE}
echo '%description' >> ${SPEC_FILE}
echo 'Sample application for test automation build deb and rpm packages' >> ${SPEC_FILE}
echo  >> ${SPEC_FILE}
echo '%files' >> ${SPEC_FILE}
echo \"/${PACKAGE_TOP_DIR}bin/${OUTPUT_BASENAME}\" >> ${SPEC_FILE}
echo '%dir' >> ${SPEC_FILE}

# Create RPM Package
cd "${TOP}"
LOG_FILE=${NBTMPDIR}/../${OUTPUT_BASENAME}.log
rpmbuild --buildroot ${TOP}/${NBTMPDIR}  -bb ${SPEC_FILE} > ${LOG_FILE}
makeDirectory "${NBTMPDIR}"
checkReturnCode
cat ${LOG_FILE}
RPM_PATH=`cat $LOG_FILE | grep '\.rpm' | tail -1 |awk -F: '{ print $2 }'`
RPM_NAME=`basename ${RPM_PATH}`
mv ${RPM_PATH} ${CND_CONF}/package
checkReturnCode
echo RPM: ${CND_CONF}/package/${RPM_NAME}

# Cleanup
cd "${TOP}"
rm -rf ${NBTMPDIR}
