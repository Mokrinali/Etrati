"""What Windows shows about ETRATI.exe (Task Manager, the file's Properties): the name a
person reads is "Etrati"; the file keeps its name. Used by ETRATI-folder.spec."""
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct,
                                                 VSVersionInfo)


def info(version: str) -> VSVersionInfo:
    nums = tuple(int(p) for p in (version.split(".") + ["0"] * 4)[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct('CompanyName', 'Etrati'),
                StringStruct('FileDescription', 'Etrati'),
                StringStruct('FileVersion', version),
                StringStruct('InternalName', 'ETRATI'),
                StringStruct('OriginalFilename', 'ETRATI.exe'),
                StringStruct('ProductName', 'Etrati'),
                StringStruct('ProductVersion', version),
            ])]),
            VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
        ])
