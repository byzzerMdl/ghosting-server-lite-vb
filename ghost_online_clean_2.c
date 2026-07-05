#include <string>
#include <winsock2.h>

namespace sf { class Packet; }

class CThreadMutex
{
public:
    CThreadMutex();
    void Lock();
    void Unlock();
};

extern void** g_pMemAlloc;

struct sf_Packet
{
    void*        vfptr;
    char*        data;
    unsigned int size;
    unsigned int capacity;
    unsigned int readPos;
    bool         isValid;
};

struct GhostData
{
    unsigned __int8 packetType;
    char  name[0x40];
    char  altName[0x20];
    char  chat[0x40];
    float velX, velY, velZ;
    float posX, posY, posZ;
    float posX2, posY2, posZ2;
    float pitch;
    float angYaw;
};

struct GhostContainer
{
    int  field0;
    int  field1;
    int  field2;
    int  field3;
    int  field4;
    GhostData*  entries;
    int         capacity;
    int         field8;
    int         count;
    void*       lastPtr;
    CThreadMutex mutex;
    bool        initialized;
};

extern SOCKET          g_hGhostSocket;
extern GhostContainer*  g_pGhostList;
extern int              g_serverIpAddr;
extern bool             g_bReceiveThreadRunning;
extern bool             g_bContainerReady;
extern int              g_ghostCount;
extern int              g_hReceiveThread;
extern int              g_connVarFlags;
extern int              g_onlineIpConVarPtr;
extern void*            Locale;
extern int              g_serverInfoPtr;

int __cdecl Tier0_Alloc(int size)
{
    static bool  s_debugInfoInit = false;
    static char* s_debugInfoStr  = 0;

    if (!s_debugInfoInit)
    {
        s_debugInfoInit = true;
        s_debugInfoStr  = (char*)0;
    }

    void* allocator = *g_pMemAlloc;
    (void)allocator; (void)size;
    return 0;
}

char* SafeStrCopy(char* dest, const char* src, int count)
{
    char* result = strncpy(dest, src, count);
    if (count > 0)
        dest[count - 1] = 0;
    return result;
}

sf_Packet* __thiscall sf_Packet_Constructor(sf_Packet* this_)
{
    this_->vfptr    = 0;
    this_->data     = 0;
    this_->size     = 0;
    this_->capacity = 0;
    this_->readPos  = 0;
    this_->isValid  = true;
    return this_;
}

int __thiscall sf_Packet_Destructor(sf_Packet* this_)
{
    this_->vfptr = 0;
    int dataPtr = (int)this_->data;
    if (dataPtr)
    {
        this_->data     = 0;
        this_->size     = 0;
        this_->capacity = 0;
    }
    return dataPtr;
}

bool __thiscall sf_Packet_ReadByte(sf_Packet* this_, unsigned char* out)
{
    bool ok = this_->isValid && (this_->readPos + 1 <= this_->size);
    this_->isValid = ok;
    if (ok)
        *out = this_->data[this_->readPos++];
    return ok;
}

bool __thiscall sf_Packet_ReadFloat(sf_Packet* this_, float* out)
{
    bool ok = this_->isValid && (this_->readPos + 4 <= this_->size);
    this_->isValid = ok;
    if (ok)
    {
        *out = *(float*)(this_->data + this_->readPos);
        this_->readPos += 4;
    }
    return ok;
}

sf_Packet* __thiscall sf_Packet_ReadString(sf_Packet* this_, std::string* out)
{
    size_t len = 0;
    bool ok = this_->isValid && (this_->readPos + 4 <= this_->size);
    this_->isValid = ok;

    if (ok)
    {
        len = ntohl(*(unsigned int*)(this_->data + this_->readPos));
        this_->readPos += 4;
    }

    out->clear();

    if (len)
    {
        ok = this_->isValid && (this_->readPos + len <= this_->size);
        this_->isValid = ok;
        if (ok)
        {
            out->assign((char*)(this_->data + this_->readPos), len);
            this_->readPos += len;
        }
    }
    return this_;
}

bool __thiscall sf_Packet_IsValid(sf_Packet* this_)
{
    return this_->isValid;
}

int __thiscall sf_Packet_BytesAvailable(sf_Packet* this_)
{
    return this_->size - this_->readPos;
}

GhostContainer* __thiscall GhostContainer_Constructor(GhostContainer* this_)
{
    this_->field0 = 0;
    this_->field1 = 0;
    this_->field2 = 0;
    this_->field3 = 0;
    this_->field4 = this_->field0;

    this_->entries  = 0;
    this_->capacity = 0;
    this_->field8   = 0;
    this_->count    = 0;
    this_->lastPtr  = this_->entries;

    g_bReceiveThreadRunning = false;
    g_bContainerReady       = true;
    g_ghostCount            = 0;

    this_->initialized = false;

    return this_;
}

void __thiscall GhostContainer_UpdateOrInsert(GhostContainer* this_, const GhostData* newEntry)
{
    this_->mutex.Lock();

    int count = this_->count;

    if (count >= 512)
    {
        int evictIndex = -1;
        int i = 0;
        unsigned char* entry = (unsigned char*)this_->entries;

        while (*entry != 1)
        {
            ++i;
            entry += sizeof(GhostData);
            if (i >= count) { evictIndex = -1; goto found; }
        }
        evictIndex = i;

    found:
        int idx = (evictIndex >= 0) ? evictIndex : 0;
        int tailCount = count - idx - 1;
        if (tailCount > 0)
        {
            memmove(&this_->entries[idx], &this_->entries[idx + 1],
                    sizeof(GhostData) * tailCount);
        }
        --this_->count;
    }

    int oldCount = this_->count;
    int cap = this_->capacity;

    if (oldCount + 1 > cap)
    {
    }

    ++this_->count;

    void* base = this_->entries;
    int shiftCount = this_->count - oldCount - 1;
    this_->lastPtr = base;
    if (shiftCount > 0)
    {
        memmove((unsigned char*)base + sizeof(GhostData) * (oldCount + 1),
                (unsigned char*)base + sizeof(GhostData) * oldCount,
                sizeof(GhostData) * shiftCount);
    }

    void* dest = (unsigned char*)this_->entries + sizeof(GhostData) * oldCount;
    if (dest)
        memcpy(dest, newEntry, sizeof(GhostData));

    this_->mutex.Unlock();
}

enum GhostPacketType
{
    GHOST_PACKET_NAME_POS    = 0,
    GHOST_PACKET_FULL_STATE  = 1,
    GHOST_PACKET_RESERVED_2  = 2,
    GHOST_PACKET_RESERVED_3  = 3,
    GHOST_PACKET_NAME_ONLY   = 4,
    GHOST_PACKET_CHAT_ONLY   = 5,
};

int __stdcall ProcessGhostPacket(GhostContainer* container, sf_Packet* pkt)
{
    unsigned char packetType;
    GhostData data;
    std::string nameBuf, chatBuf;
    int result;

    if (!sf_Packet_ReadByte(pkt, &packetType) || !sf_Packet_IsValid(pkt))
        return 0;

    memset(&data, 0, sizeof(data));
    data.packetType = packetType;
    result = packetType;

    switch (packetType)
    {
    case GHOST_PACKET_NAME_POS:
    {
        if (!sf_Packet_ReadString(pkt, &nameBuf) || !sf_Packet_IsValid(pkt))
            goto cleanup;
        unsigned char tmp;
        sf_Packet_ReadByte(pkt, &tmp);
        sf_Packet_ReadByte(pkt, &tmp);
        sf_Packet_ReadByte(pkt, &tmp);
        sf_Packet_ReadByte(pkt, &tmp);
        sf_Packet_ReadByte(pkt, &tmp);
        sf_Packet_ReadByte(pkt, &tmp);
        if (!sf_Packet_ReadByte(pkt, &tmp) || !sf_Packet_IsValid(pkt))
            goto cleanup;
        SafeStrCopy(data.name, nameBuf.c_str(), 0x40);
        result = GhostContainer_UpdateOrInsert(container, &data), 0;
        break;
    }

    case GHOST_PACKET_FULL_STATE:
    {
        sf_Packet_ReadString(pkt, &nameBuf);
        sf_Packet_ReadString(pkt, &chatBuf);
        sf_Packet_ReadFloat(pkt, &data.velX);
        sf_Packet_ReadFloat(pkt, &data.velY);
        sf_Packet_ReadFloat(pkt, &data.velZ);
        sf_Packet_ReadFloat(pkt, &data.posX2);
        sf_Packet_ReadFloat(pkt, &data.posY2);
        sf_Packet_ReadFloat(pkt, &data.posZ2);
        sf_Packet_ReadFloat(pkt, &data.pitch);

        if (!sf_Packet_IsValid(pkt))
            goto cleanup;

        SafeStrCopy(data.name,    nameBuf.c_str(), 0x40);
        SafeStrCopy(data.chat,    chatBuf.c_str(), 0x20);
        SafeStrCopy(data.altName, nameBuf.c_str(), 0x20);

        GhostContainer_UpdateOrInsert(container, &data);
        result = 0;
        break;
    }

    case GHOST_PACKET_RESERVED_2:
    case GHOST_PACKET_RESERVED_3:
        goto cleanup;

    case GHOST_PACKET_NAME_ONLY:
    {
        if (!sf_Packet_ReadString(pkt, &nameBuf) || !sf_Packet_IsValid(pkt))
            goto cleanup;
        SafeStrCopy(data.name, nameBuf.c_str(), 0x40);
        GhostContainer_UpdateOrInsert(container, &data);
        result = 0;
        break;
    }

    case GHOST_PACKET_CHAT_ONLY:
    {
        if (sf_Packet_ReadString(pkt, &chatBuf) && sf_Packet_IsValid(pkt))
        {
            SafeStrCopy(data.chat, chatBuf.c_str(), 0x40);
            GhostContainer_UpdateOrInsert(container, &data);
            result = 0;
        }
        break;
    }

    default:
        return result;
    }

cleanup:
    return result;
}

int GhostOnline_ReceiveThread()
{
    sf_Packet packet;
    unsigned long senderAddr;
    unsigned short senderPort[2];

    while (g_bReceiveThreadRunning)
    {
        sf_Packet_Constructor(&packet);

        bool recvFailed = true;
        if (recvFailed || !sf_Packet_BytesAvailable(&packet))
        {
        }
        else if (senderAddr == (unsigned long)g_serverIpAddr)
        {
            if (!g_pGhostList)
            {
                void* mem = 0;
                g_pGhostList = mem ? GhostContainer_Constructor((GhostContainer*)mem) : 0;
            }
            ProcessGhostPacket(g_pGhostList, &packet);
        }

        sf_Packet_Destructor(&packet);
    }

    return 0;
}

int GhostOnline_Connect()
{
    if (g_bReceiveThreadRunning)
    {
        return 0;
    }

    const char* hostStr;
    if (g_connVarFlags & 0x1000)
        hostStr = "FCVAR_NEVER_AS_STRING";
    else
    {
        hostStr = (const char*)Locale;
    }

    if (!g_pGhostList)
    {
        void* mem = 0;
        g_pGhostList = mem ? GhostContainer_Constructor((GhostContainer*)mem) : 0;
    }

    g_pGhostList->mutex.Lock();
    g_pGhostList->count = 0;
    g_pGhostList->mutex.Unlock();

    g_bContainerReady       = true;
    g_ghostCount            = 0;
    g_bReceiveThreadRunning = true;

    return 0;
}
