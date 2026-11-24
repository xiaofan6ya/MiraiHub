import{h as ft,k as b,i as ua,m as Ea,F as Fe,c as T,r as k,w as De,o as da,D as Na,j as Oa,H as Ba,G as Fa,I as Ha,n as At,p as Ma,t as pe,b as Wa,V as Z,R as c,Q as g,S as a,$ as qa,O as E,f as H,X as x,W as y,Z as st,P as ne,Y as ke}from"./vendor-vue-01Qz8dz3.js";import{J as ja,K as pt,L as Ua,m as Ka,M as Da,v as Va,O as Ga,x as Xa,Q as Qa,n as f,o as S,q as O,p as D,r as Ja,R as Lt,T as Vt,y as Ya,z as ca,V as Za,W as en,E as fe,D as rt,P as tn,H as Gt,N as Te,X as an,u as nn,C as sn,I as rn}from"./index-BvVhdLnX.js";import{u as on,B as J,E as ot,a as ln}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-B16dhayw.js";import{I as un}from"./IconBtn-CgfqCEXM.js";import{C as Xt}from"./Card-DPgSK7ht.js";import{S as lt}from"./Section-BsL4QYVc.js";import{B as it}from"./Badge-BS1svIkr.js";/* empty css                                                                  */import{C as Qt}from"./Chip-wFQ1Lvkx.js";import{C as ut}from"./Copyable-BfO41Hx9.js";import{S as dn}from"./Segmented-EkGFCB4T.js";import{K as cn}from"./KV-DYjSCK1r.js";import{C as Re}from"./CodeBlock-BO_0dqNP.js";import{D as Jt,N as It}from"./DataTable-um8YCfW2.js";/* empty css                                                              */import{F as pn}from"./FilterBar-BRmGLZsu.js";import{M as Yt}from"./Meter-CsSi6kwA.js";import{R as fn}from"./RowActions-Do-dQ6Za.js";import{h as ee,j as mn,A as bn,_ as vn}from"./_plugin-vue_export-helper-ND5k2sci.js";import{F as gn}from"./FormDrawer-BQ_NEiFG.js";import{F as me}from"./FormField-Bdmg9AwS.js";import{D as dt}from"./DetailDrawer-B85Js5pd.js";import{e as Et,f as Oe}from"./error-D0KPpZSz.js";import{u as yn}from"./useConfirm-G6gzkYt5.js";import{u as hn}from"./useTableQuery-DG7Bhkkg.js";import{F as He,f as pa,e as Pe,m as kn,c as xe}from"./format-ByabA-4O.js";import{R as xn}from"./refresh-cw-DzeQqB87.js";import{P as Zt}from"./plus-BpdV4Pha.js";import{A as _n}from"./Add-BYamtQox.js";import{V as Nt,E as wn,q as Cn,G as Sn,v as ea,j as Tn,g as ct}from"./vendor-naive-SrciwSnN.js";import{N as Be}from"./Select-CiY43QJ-.js";import{N as ta}from"./Skeleton-CMofO7BY.js";/* empty css                                                                */import"./Spin-DtoLZcf5.js";import"./feedback-BKqEpFG1.js";/* empty css                                                                 */import"./media-DhhT9Mh5.js";import"./Forward-CyWRo3OE.js";import"./Dropdown-CgPJSYRd.js";import"./x-7zblgmJT.js";import"./ellipsis-D0wrKlJa.js";var Rn=/\s/;function Pn(t){for(var e=t.length;e--&&Rn.test(t.charAt(e)););return e}var zn=/^\s+/;function $n(t){return t&&t.slice(0,Pn(t)+1).replace(zn,"")}var aa=NaN,An=/^[-+]0x[0-9a-f]+$/i,Ln=/^0b[01]+$/i,In=/^0o[0-7]+$/i,En=parseInt;function na(t){if(typeof t=="number")return t;if(ja(t))return aa;if(pt(t)){var e=typeof t.valueOf=="function"?t.valueOf():t;t=pt(e)?e+"":e}if(typeof t!="string")return t===0?t:+t;t=$n(t);var r=Ln.test(t);return r||In.test(t)?En(t.slice(2),r?2:8):An.test(t)?aa:+t}var Ot=function(){return Ua.Date.now()},Nn="Expected a function",On=Math.max,Bn=Math.min;function Fn(t,e,r){var u,p,d,h,m,_,P=0,z=!1,A=!1,q=!0;if(typeof t!="function")throw new TypeError(Nn);e=na(e)||0,pt(r)&&(z=!!r.leading,A="maxWait"in r,d=A?On(na(r.maxWait)||0,e):d,q="trailing"in r?!!r.trailing:q);function N(w){var W=u,te=p;return u=p=void 0,P=w,h=t.apply(te,W),h}function B(w){return P=w,m=setTimeout(U,e),z?N(w):h}function F(w){var W=w-_,te=w-P,oe=e-W;return A?Bn(oe,d-te):oe}function j(w){var W=w-_,te=w-P;return _===void 0||W>=e||W<0||A&&te>=d}function U(){var w=Ot();if(j(w))return M(w);m=setTimeout(U,F(w))}function M(w){return m=void 0,q&&u?N(w):(u=p=void 0,h)}function re(){m!==void 0&&clearTimeout(m),P=0,u=_=p=m=void 0}function Y(){return m===void 0?h:M(Ot())}function R(){var w=Ot(),W=j(w);if(u=arguments,p=this,_=w,W){if(m===void 0)return B(_);if(A)return clearTimeout(m),m=setTimeout(U,e),N(_)}return m===void 0&&(m=setTimeout(U,e)),h}return R.cancel=re,R.flush=Y,R}var Hn="Expected a function";function Mn(t,e,r){var u=!0,p=!0;if(typeof t!="function")throw new TypeError(Hn);return pt(r)&&(u="leading"in r?!!r.leading:u,p="trailing"in r?!!r.trailing:p),Fn(t,e,{leading:u,maxWait:e,trailing:p})}const jt=Ka("n-tabs"),fa={tab:[String,Number,Object,Function],name:{type:[String,Number],required:!0},disabled:Boolean,displayDirective:{type:String,default:"if"},closable:{type:Boolean,default:void 0},tabProps:Object,label:[String,Number,Object,Function]},Ke=ft({__TAB_PANE__:!0,name:"TabPane",alias:["TabPanel"],props:fa,slots:Object,setup(t){const e=ua(jt,null);return e||Da("tab-pane","`n-tab-pane` must be placed inside `n-tabs`."),{style:e.paneStyleRef,class:e.paneClassRef,mergedClsPrefix:e.mergedClsPrefixRef}},render(){return b("div",{class:[`${this.mergedClsPrefix}-tab-pane`,this.class],style:this.style},this.$slots)}}),Wn=Object.assign({internalLeftPadded:Boolean,internalAddable:Boolean,internalCreatedByPane:Boolean},Qa(fa,["displayDirective"])),Wt=ft({__TAB__:!0,inheritAttrs:!1,name:"Tab",props:Wn,setup(t){const{mergedClsPrefixRef:e,valueRef:r,typeRef:u,closableRef:p,tabStyleRef:d,addTabStyleRef:h,tabClassRef:m,addTabClassRef:_,tabChangeIdRef:P,onBeforeLeaveRef:z,triggerRef:A,handleAdd:q,activateTab:N,handleClose:B}=ua(jt);return{trigger:A,mergedClosable:T(()=>{if(t.internalAddable)return!1;const{closable:F}=t;return F===void 0?p.value:F}),style:d,addStyle:h,tabClass:m,addTabClass:_,clsPrefix:e,value:r,type:u,handleClose(F){F.stopPropagation(),!t.disabled&&B(t.name)},activateTab(){if(t.disabled)return;if(t.internalAddable){q();return}const{name:F}=t,j=++P.id;if(F!==r.value){const{value:U}=z;U?Promise.resolve(U(t.name,r.value)).then(M=>{M&&P.id===j&&N(F)}):N(F)}}}},render(){const{internalAddable:t,clsPrefix:e,name:r,disabled:u,label:p,tab:d,value:h,mergedClosable:m,trigger:_,$slots:{default:P}}=this,z=p??d;return b("div",{class:`${e}-tabs-tab-wrapper`},this.internalLeftPadded?b("div",{class:`${e}-tabs-tab-pad`}):null,b("div",Object.assign({key:r,"data-name":r,"data-disabled":u?!0:void 0},Ea({class:[`${e}-tabs-tab`,h===r&&`${e}-tabs-tab--active`,u&&`${e}-tabs-tab--disabled`,m&&`${e}-tabs-tab--closable`,t&&`${e}-tabs-tab--addable`,t?this.addTabClass:this.tabClass],onClick:_==="click"?this.activateTab:void 0,onMouseenter:_==="hover"?this.activateTab:void 0,style:t?this.addStyle:this.style},this.internalCreatedByPane?this.tabProps||{}:this.$attrs)),b("span",{class:`${e}-tabs-tab__label`},t?b(Fe,null,b("div",{class:`${e}-tabs-tab__height-placeholder`}," "),b(Va,{clsPrefix:e},{default:()=>b(_n,null)})):P?P():typeof z=="object"?z:Ga(z??r)),m&&this.type==="card"?b(Xa,{clsPrefix:e,class:`${e}-tabs-tab__close`,onClick:this.handleClose,disabled:u}):null))}}),qn=f("tabs",`
 box-sizing: border-box;
 width: 100%;
 display: flex;
 flex-direction: column;
 transition:
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
`,[S("segment-type",[f("tabs-rail",[O("&.transition-disabled",[f("tabs-capsule",`
 transition: none;
 `)])])]),S("top",[f("tab-pane",`
 padding: var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left);
 `)]),S("left",[f("tab-pane",`
 padding: var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left) var(--n-pane-padding-top);
 `)]),S("left, right",`
 flex-direction: row;
 `,[f("tabs-bar",`
 width: 2px;
 right: 0;
 transition:
 top .2s var(--n-bezier),
 max-height .2s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),f("tabs-tab",`
 padding: var(--n-tab-padding-vertical); 
 `)]),S("right",`
 flex-direction: row-reverse;
 `,[f("tab-pane",`
 padding: var(--n-pane-padding-left) var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom);
 `),f("tabs-bar",`
 left: 0;
 `)]),S("bottom",`
 flex-direction: column-reverse;
 justify-content: flex-end;
 `,[f("tab-pane",`
 padding: var(--n-pane-padding-bottom) var(--n-pane-padding-right) var(--n-pane-padding-top) var(--n-pane-padding-left);
 `),f("tabs-bar",`
 top: 0;
 `)]),f("tabs-rail",`
 position: relative;
 padding: 3px;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 background-color: var(--n-color-segment);
 transition: background-color .3s var(--n-bezier);
 display: flex;
 align-items: center;
 `,[f("tabs-capsule",`
 border-radius: var(--n-tab-border-radius);
 position: absolute;
 pointer-events: none;
 background-color: var(--n-tab-color-segment);
 box-shadow: 0 1px 3px 0 rgba(0, 0, 0, .08);
 transition: transform 0.3s var(--n-bezier);
 `),f("tabs-tab-wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[f("tabs-tab",`
 overflow: hidden;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[S("active",`
 font-weight: var(--n-font-weight-strong);
 color: var(--n-tab-text-color-active);
 `),O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])])]),S("flex",[f("tabs-nav",`
 width: 100%;
 position: relative;
 `,[f("tabs-wrapper",`
 width: 100%;
 `,[f("tabs-tab",`
 margin-right: 0;
 `)])])]),f("tabs-nav",`
 box-sizing: border-box;
 line-height: 1.5;
 display: flex;
 transition: border-color .3s var(--n-bezier);
 `,[D("prefix, suffix",`
 display: flex;
 align-items: center;
 `),D("prefix","padding-right: 16px;"),D("suffix","padding-left: 16px;")]),S("top, bottom",[O(">",[f("tabs-nav",[f("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 bottom: 0;
 left: 0;
 width: 20px;
 `),O("&::after",`
 top: 0;
 bottom: 0;
 right: 0;
 width: 20px;
 `),S("shadow-start",[O("&::before",`
 box-shadow: inset 10px 0 8px -8px rgba(0, 0, 0, .12);
 `)]),S("shadow-end",[O("&::after",`
 box-shadow: inset -10px 0 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),S("left, right",[f("tabs-nav-scroll-content",`
 flex-direction: column;
 `),O(">",[f("tabs-nav",[f("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),O("&::after",`
 bottom: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),S("shadow-start",[O("&::before",`
 box-shadow: inset 0 10px 8px -8px rgba(0, 0, 0, .12);
 `)]),S("shadow-end",[O("&::after",`
 box-shadow: inset 0 -10px 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),f("tabs-nav-scroll-wrapper",`
 flex: 1;
 position: relative;
 overflow: hidden;
 `,[f("tabs-nav-y-scroll",`
 height: 100%;
 width: 100%;
 overflow-y: auto; 
 scrollbar-width: none;
 `,[O("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",`
 width: 0;
 height: 0;
 display: none;
 `)]),O("&::before, &::after",`
 transition: box-shadow .3s var(--n-bezier);
 pointer-events: none;
 content: "";
 position: absolute;
 z-index: 1;
 `)]),f("tabs-nav-scroll-content",`
 display: flex;
 position: relative;
 min-width: 100%;
 min-height: 100%;
 width: fit-content;
 box-sizing: border-box;
 `),f("tabs-wrapper",`
 display: inline-flex;
 flex-wrap: nowrap;
 position: relative;
 `),f("tabs-tab-wrapper",`
 display: flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 flex-grow: 0;
 `),f("tabs-tab",`
 cursor: pointer;
 white-space: nowrap;
 flex-wrap: nowrap;
 display: inline-flex;
 align-items: center;
 color: var(--n-tab-text-color);
 font-size: var(--n-tab-font-size);
 background-clip: padding-box;
 padding: var(--n-tab-padding);
 transition:
 box-shadow .3s var(--n-bezier),
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[S("disabled",{cursor:"not-allowed"}),D("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),D("label",`
 display: flex;
 align-items: center;
 z-index: 1;
 `)]),f("tabs-bar",`
 position: absolute;
 bottom: 0;
 height: 2px;
 border-radius: 1px;
 background-color: var(--n-bar-color);
 transition:
 left .2s var(--n-bezier),
 max-width .2s var(--n-bezier),
 opacity .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `,[O("&.transition-disabled",`
 transition: none;
 `),S("disabled",`
 background-color: var(--n-tab-text-color-disabled)
 `)]),f("tabs-pane-wrapper",`
 position: relative;
 overflow: hidden;
 transition: max-height .2s var(--n-bezier);
 `),f("tab-pane",`
 color: var(--n-pane-text-color);
 width: 100%;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .2s var(--n-bezier);
 left: 0;
 right: 0;
 top: 0;
 `,[O("&.next-transition-leave-active, &.prev-transition-leave-active, &.next-transition-enter-active, &.prev-transition-enter-active",`
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 transform .2s var(--n-bezier),
 opacity .2s var(--n-bezier);
 `),O("&.next-transition-leave-active, &.prev-transition-leave-active",`
 position: absolute;
 `),O("&.next-transition-enter-from, &.prev-transition-leave-to",`
 transform: translateX(32px);
 opacity: 0;
 `),O("&.next-transition-leave-to, &.prev-transition-enter-from",`
 transform: translateX(-32px);
 opacity: 0;
 `),O("&.next-transition-leave-from, &.next-transition-enter-to, &.prev-transition-leave-from, &.prev-transition-enter-to",`
 transform: translateX(0);
 opacity: 1;
 `)]),f("tabs-tab-pad",`
 box-sizing: border-box;
 width: var(--n-tab-gap);
 flex-grow: 0;
 flex-shrink: 0;
 `),S("line-type, bar-type",[f("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 box-sizing: border-box;
 vertical-align: bottom;
 `,[O("&:hover",{color:"var(--n-tab-text-color-hover)"}),S("active",`
 color: var(--n-tab-text-color-active);
 font-weight: var(--n-tab-font-weight-active);
 `),S("disabled",{color:"var(--n-tab-text-color-disabled)"})])]),f("tabs-nav",[S("line-type",[S("top",[D("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),f("tabs-nav-scroll-content",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),f("tabs-bar",`
 bottom: -1px;
 `)]),S("left",[D("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),f("tabs-nav-scroll-content",`
 border-right: 1px solid var(--n-tab-border-color);
 `),f("tabs-bar",`
 right: -1px;
 `)]),S("right",[D("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),f("tabs-nav-scroll-content",`
 border-left: 1px solid var(--n-tab-border-color);
 `),f("tabs-bar",`
 left: -1px;
 `)]),S("bottom",[D("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),f("tabs-nav-scroll-content",`
 border-top: 1px solid var(--n-tab-border-color);
 `),f("tabs-bar",`
 top: -1px;
 `)]),D("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),f("tabs-nav-scroll-content",`
 transition: border-color .3s var(--n-bezier);
 `),f("tabs-bar",`
 border-radius: 0;
 `)]),S("card-type",[D("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),f("tabs-pad",`
 flex-grow: 1;
 transition: border-color .3s var(--n-bezier);
 `),f("tabs-tab-pad",`
 transition: border-color .3s var(--n-bezier);
 `),f("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 border: 1px solid var(--n-tab-border-color);
 background-color: var(--n-tab-color);
 box-sizing: border-box;
 position: relative;
 vertical-align: bottom;
 display: flex;
 justify-content: space-between;
 font-size: var(--n-tab-font-size);
 color: var(--n-tab-text-color);
 `,[S("addable",`
 padding-left: 8px;
 padding-right: 8px;
 font-size: 16px;
 justify-content: center;
 `,[D("height-placeholder",`
 width: 0;
 font-size: var(--n-tab-font-size);
 `),Ja("disabled",[O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])]),S("closable","padding-right: 8px;"),S("active",`
 background-color: #0000;
 font-weight: var(--n-tab-font-weight-active);
 color: var(--n-tab-text-color-active);
 `),S("disabled","color: var(--n-tab-text-color-disabled);")])]),S("left, right",`
 flex-direction: column; 
 `,[D("prefix, suffix",`
 padding: var(--n-tab-padding-vertical);
 `),f("tabs-wrapper",`
 flex-direction: column;
 `),f("tabs-tab-wrapper",`
 flex-direction: column;
 `,[f("tabs-tab-pad",`
 height: var(--n-tab-gap-vertical);
 width: 100%;
 `)])]),S("top",[S("card-type",[f("tabs-scroll-padding","border-bottom: 1px solid var(--n-tab-border-color);"),D("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),f("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-top-right-radius: var(--n-tab-border-radius);
 `,[S("active",`
 border-bottom: 1px solid #0000;
 `)]),f("tabs-tab-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),f("tabs-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `)])]),S("left",[S("card-type",[f("tabs-scroll-padding","border-right: 1px solid var(--n-tab-border-color);"),D("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),f("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-bottom-left-radius: var(--n-tab-border-radius);
 `,[S("active",`
 border-right: 1px solid #0000;
 `)]),f("tabs-tab-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `),f("tabs-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `)])]),S("right",[S("card-type",[f("tabs-scroll-padding","border-left: 1px solid var(--n-tab-border-color);"),D("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),f("tabs-tab",`
 border-top-right-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[S("active",`
 border-left: 1px solid #0000;
 `)]),f("tabs-tab-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `),f("tabs-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `)])]),S("bottom",[S("card-type",[f("tabs-scroll-padding","border-top: 1px solid var(--n-tab-border-color);"),D("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),f("tabs-tab",`
 border-bottom-left-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[S("active",`
 border-top: 1px solid #0000;
 `)]),f("tabs-tab-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `),f("tabs-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `)])])])]),Bt=Mn,jn=Object.assign(Object.assign({},ca.props),{value:[String,Number],defaultValue:[String,Number],trigger:{type:String,default:"click"},type:{type:String,default:"bar"},closable:Boolean,justifyContent:String,size:String,placement:{type:String,default:"top"},tabStyle:[String,Object],tabClass:String,addTabStyle:[String,Object],addTabClass:String,barWidth:Number,paneClass:String,paneStyle:[String,Object],paneWrapperClass:String,paneWrapperStyle:[String,Object],addable:[Boolean,Object],tabsPadding:{type:Number,default:0},animated:Boolean,onBeforeLeave:Function,onAdd:Function,"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array],onClose:[Function,Array],labelSize:String,activeName:[String,Number],onActiveNameChange:[Function,Array]}),Un=ft({name:"Tabs",props:jn,slots:Object,setup(t,{slots:e}){var r,u,p,d;const{mergedClsPrefixRef:h,inlineThemeDisabled:m,mergedComponentPropsRef:_}=Ya(t),P=ca("Tabs","-tabs",qn,Za,t,h),z=k(null),A=k(null),q=k(null),N=k(null),B=k(null),F=k(null),j=k(!0),U=k(!0),M=ea(t,["labelSize","size"]),re=T(()=>{var l,i;if(M.value)return M.value;const v=(i=(l=_?.value)===null||l===void 0?void 0:l.Tabs)===null||i===void 0?void 0:i.size;return v||"medium"}),Y=ea(t,["activeName","value"]),R=k((u=(r=Y.value)!==null&&r!==void 0?r:t.defaultValue)!==null&&u!==void 0?u:e.default?(d=(p=Lt(e.default())[0])===null||p===void 0?void 0:p.props)===null||d===void 0?void 0:d.name:null),w=Cn(Y,R),W={id:0},te=T(()=>{if(!(!t.justifyContent||t.type==="card"))return{display:"flex",justifyContent:t.justifyContent}});De(w,()=>{W.id=0,ze(),le()});function oe(){var l;const{value:i}=w;return i===null?null:(l=z.value)===null||l===void 0?void 0:l.querySelector(`[data-name="${i}"]`)}function Ve(l){if(t.type==="card")return;const{value:i}=A;if(!i)return;const v=i.style.opacity==="0";if(l){const C=`${h.value}-tabs-bar--disabled`,{barWidth:I,placement:V}=t;if(l.dataset.disabled==="true"?i.classList.add(C):i.classList.remove(C),["top","bottom"].includes(V)){if(Ge(["top","maxHeight","height"]),typeof I=="number"&&l.offsetWidth>=I){const Q=Math.floor((l.offsetWidth-I)/2)+l.offsetLeft;i.style.left=`${Q}px`,i.style.maxWidth=`${I}px`}else i.style.left=`${l.offsetLeft}px`,i.style.maxWidth=`${l.offsetWidth}px`;i.style.width="8192px",v&&(i.style.transition="none"),i.offsetWidth,v&&(i.style.transition="",i.style.opacity="1")}else{if(Ge(["left","maxWidth","width"]),typeof I=="number"&&l.offsetHeight>=I){const Q=Math.floor((l.offsetHeight-I)/2)+l.offsetTop;i.style.top=`${Q}px`,i.style.maxHeight=`${I}px`}else i.style.top=`${l.offsetTop}px`,i.style.maxHeight=`${l.offsetHeight}px`;i.style.height="8192px",v&&(i.style.transition="none"),i.offsetHeight,v&&(i.style.transition="",i.style.opacity="1")}}}function be(){if(t.type==="card")return;const{value:l}=A;l&&(l.style.opacity="0")}function Ge(l){const{value:i}=A;if(i)for(const v of l)i.style[v]=""}function ze(){if(t.type==="card")return;const l=oe();l?Ve(l):be()}function le(){var l;const i=(l=B.value)===null||l===void 0?void 0:l.$el;if(!i)return;const v=oe();if(!v)return;const{scrollLeft:C,offsetWidth:I}=i,{offsetLeft:V,offsetWidth:Q}=v;C>V?i.scrollTo({top:0,left:V,behavior:"smooth"}):V+Q>C+I&&i.scrollTo({top:0,left:V+Q-I,behavior:"smooth"})}const $e=k(null);let se=0,ie=null;function mt(l){const i=$e.value;if(i){se=l.getBoundingClientRect().height;const v=`${se}px`,C=()=>{i.style.height=v,i.style.maxHeight=v};ie?(C(),ie(),ie=null):ie=C}}function Xe(l){const i=$e.value;if(i){const v=l.getBoundingClientRect().height,C=()=>{document.body.offsetHeight,i.style.maxHeight=`${v}px`,i.style.height=`${Math.max(se,v)}px`};ie?(ie(),ie=null,C()):ie=C}}function ye(){const l=$e.value;if(l){l.style.maxHeight="",l.style.height="";const{paneWrapperStyle:i}=t;if(typeof i=="string")l.style.cssText=i;else if(i){const{maxHeight:v,height:C}=i;v!==void 0&&(l.style.maxHeight=v),C!==void 0&&(l.style.height=C)}}}const X={value:[]},$=k("next");function ue(l){const i=w.value;let v="next";for(const C of X.value){if(C===i)break;if(C===l){v="prev";break}}$.value=v,Me(l)}function Me(l){const{onActiveNameChange:i,onUpdateValue:v,"onUpdate:value":C}=t;i&&rt(i,l),v&&rt(v,l),C&&rt(C,l),R.value=l}function Qe(l){const{onClose:i}=t;i&&rt(i,l)}function _e(){const{value:l}=A;if(!l)return;const i="transition-disabled";l.classList.add(i),ze(),l.classList.remove(i)}const ae=k(null);function we({transitionDisabled:l}){const i=z.value;if(!i)return;l&&i.classList.add("transition-disabled");const v=oe();v&&ae.value&&(ae.value.style.width=`${v.offsetWidth}px`,ae.value.style.height=`${v.offsetHeight}px`,ae.value.style.transform=`translateX(${v.offsetLeft-Tn(getComputedStyle(i).paddingLeft)}px)`,l&&ae.value.offsetWidth),l&&i.classList.remove("transition-disabled")}De([w],()=>{t.type==="segment"&&At(()=>{we({transitionDisabled:!1})})}),da(()=>{t.type==="segment"&&we({transitionDisabled:!0})});let Ae=0;function We(l){var i;if(l.contentRect.width===0&&l.contentRect.height===0||Ae===l.contentRect.width)return;Ae=l.contentRect.width;const{type:v}=t;if((v==="line"||v==="bar")&&_e(),v!=="segment"){const{placement:C}=t;qe((C==="top"||C==="bottom"?(i=B.value)===null||i===void 0?void 0:i.$el:F.value)||null)}}const Le=Bt(We,64);De([()=>t.justifyContent,()=>t.size],()=>{At(()=>{const{type:l}=t;(l==="line"||l==="bar")&&_e()})});const K=k(!1);function Je(l){var i;const{target:v,contentRect:{width:C,height:I}}=l,V=v.parentElement.parentElement.offsetWidth,Q=v.parentElement.parentElement.offsetHeight,{placement:G}=t;if(!K.value)G==="top"||G==="bottom"?V<C&&(K.value=!0):Q<I&&(K.value=!0);else{const{value:ve}=N;if(!ve)return;G==="top"||G==="bottom"?V-C>ve.$el.offsetWidth&&(K.value=!1):Q-I>ve.$el.offsetHeight&&(K.value=!1)}qe(((i=B.value)===null||i===void 0?void 0:i.$el)||null)}const Ie=Bt(Je,64);function bt(){const{onAdd:l}=t;l&&l(),At(()=>{const i=oe(),{value:v}=B;!i||!v||v.scrollTo({left:i.offsetLeft,top:0,behavior:"smooth"})})}function qe(l){if(!l)return;const{placement:i}=t;if(i==="top"||i==="bottom"){const{scrollLeft:v,scrollWidth:C,offsetWidth:I}=l;j.value=v<=0,U.value=v+I>=C}else{const{scrollTop:v,scrollHeight:C,offsetHeight:I}=l;j.value=v<=0,U.value=v+I>=C}}const vt=Bt(l=>{qe(l.target)},64);Ma(jt,{triggerRef:pe(t,"trigger"),tabStyleRef:pe(t,"tabStyle"),tabClassRef:pe(t,"tabClass"),addTabStyleRef:pe(t,"addTabStyle"),addTabClassRef:pe(t,"addTabClass"),paneClassRef:pe(t,"paneClass"),paneStyleRef:pe(t,"paneStyle"),mergedClsPrefixRef:h,typeRef:pe(t,"type"),closableRef:pe(t,"closable"),valueRef:w,tabChangeIdRef:W,onBeforeLeaveRef:pe(t,"onBeforeLeave"),activateTab:ue,handleClose:Qe,handleAdd:bt}),Sn(()=>{ze(),le()}),Na(()=>{const{value:l}=q;if(!l)return;const{value:i}=h,v=`${i}-tabs-nav-scroll-wrapper--shadow-start`,C=`${i}-tabs-nav-scroll-wrapper--shadow-end`;j.value?l.classList.remove(v):l.classList.add(v),U.value?l.classList.remove(C):l.classList.add(C)});const gt={syncBarPosition:()=>{ze()}},yt=()=>{we({transitionDisabled:!0})},Ce=T(()=>{const{value:l}=re,{type:i}=t,v={card:"Card",bar:"Bar",line:"Line",segment:"Segment"}[i],C=`${l}${v}`,{self:{barColor:I,closeIconColor:V,closeIconColorHover:Q,closeIconColorPressed:G,tabColor:ve,tabBorderColor:Ee,paneTextColor:je,tabFontWeight:Ne,tabBorderRadius:Ye,tabFontWeightActive:ht,colorSegment:Ze,fontWeightStrong:kt,tabColorSegment:xt,closeSize:ce,closeIconSize:et,closeColorHover:tt,closeColorPressed:_t,closeBorderRadius:wt,[fe("panePadding",l)]:he,[fe("tabPadding",C)]:Ct,[fe("tabPaddingVertical",C)]:Se,[fe("tabGap",C)]:St,[fe("tabGap",`${C}Vertical`)]:Tt,[fe("tabTextColor",i)]:Rt,[fe("tabTextColorActive",i)]:at,[fe("tabTextColorHover",i)]:Ue,[fe("tabTextColorDisabled",i)]:Pt,[fe("tabFontSize",l)]:zt},common:{cubicBezierEaseInOut:nt}}=P.value;return{"--n-bezier":nt,"--n-color-segment":Ze,"--n-bar-color":I,"--n-tab-font-size":zt,"--n-tab-text-color":Rt,"--n-tab-text-color-active":at,"--n-tab-text-color-disabled":Pt,"--n-tab-text-color-hover":Ue,"--n-pane-text-color":je,"--n-tab-border-color":Ee,"--n-tab-border-radius":Ye,"--n-close-size":ce,"--n-close-icon-size":et,"--n-close-color-hover":tt,"--n-close-color-pressed":_t,"--n-close-border-radius":wt,"--n-close-icon-color":V,"--n-close-icon-color-hover":Q,"--n-close-icon-color-pressed":G,"--n-tab-color":ve,"--n-tab-font-weight":Ne,"--n-tab-font-weight-active":ht,"--n-tab-padding":Ct,"--n-tab-padding-vertical":Se,"--n-tab-gap":St,"--n-tab-gap-vertical":Tt,"--n-pane-padding-left":ct(he,"left"),"--n-pane-padding-right":ct(he,"right"),"--n-pane-padding-top":ct(he,"top"),"--n-pane-padding-bottom":ct(he,"bottom"),"--n-font-weight-strong":kt,"--n-tab-color-segment":xt}}),de=m?en("tabs",T(()=>`${re.value[0]}${t.type[0]}`),Ce,t):void 0;return Object.assign({mergedClsPrefix:h,mergedValue:w,renderedNames:new Set,segmentCapsuleElRef:ae,tabsPaneWrapperRef:$e,tabsElRef:z,barElRef:A,addTabInstRef:N,xScrollInstRef:B,scrollWrapperElRef:q,addTabFixed:K,tabWrapperStyle:te,handleNavResize:Le,mergedSize:re,handleScroll:vt,handleTabsResize:Ie,cssVars:m?void 0:Ce,themeClass:de?.themeClass,animationDirection:$,renderNameListRef:X,yScrollElRef:F,handleSegmentResize:yt,onAnimationBeforeLeave:mt,onAnimationEnter:Xe,onAnimationAfterEnter:ye,onRender:de?.onRender},gt)},render(){const{mergedClsPrefix:t,type:e,placement:r,addTabFixed:u,addable:p,mergedSize:d,renderNameListRef:h,onRender:m,paneWrapperClass:_,paneWrapperStyle:P,$slots:{default:z,prefix:A,suffix:q}}=this;m?.();const N=z?Lt(z()).filter(R=>R.type.__TAB_PANE__===!0):[],B=z?Lt(z()).filter(R=>R.type.__TAB__===!0):[],F=!B.length,j=e==="card",U=e==="segment",M=!j&&!U&&this.justifyContent;h.value=[];const re=()=>{const R=b("div",{style:this.tabWrapperStyle,class:`${t}-tabs-wrapper`},M?null:b("div",{class:`${t}-tabs-scroll-padding`,style:r==="top"||r==="bottom"?{width:`${this.tabsPadding}px`}:{height:`${this.tabsPadding}px`}}),F?N.map((w,W)=>(h.value.push(w.props.name),Ft(b(Wt,Object.assign({},w.props,{internalCreatedByPane:!0,internalLeftPadded:W!==0&&(!M||M==="center"||M==="start"||M==="end")}),w.children?{default:w.children.tab}:void 0)))):B.map((w,W)=>(h.value.push(w.props.name),Ft(W!==0&&!M?oa(w):w))),!u&&p&&j?ra(p,(F?N.length:B.length)!==0):null,M?null:b("div",{class:`${t}-tabs-scroll-padding`,style:{width:`${this.tabsPadding}px`}}));return b("div",{ref:"tabsElRef",class:`${t}-tabs-nav-scroll-content`},j&&p?b(Nt,{onResize:this.handleTabsResize},{default:()=>R}):R,j?b("div",{class:`${t}-tabs-pad`}):null,j?null:b("div",{ref:"barElRef",class:`${t}-tabs-bar`}))},Y=U?"top":r;return b("div",{class:[`${t}-tabs`,this.themeClass,`${t}-tabs--${e}-type`,`${t}-tabs--${d}-size`,M&&`${t}-tabs--flex`,`${t}-tabs--${Y}`],style:this.cssVars},b("div",{class:[`${t}-tabs-nav--${e}-type`,`${t}-tabs-nav--${Y}`,`${t}-tabs-nav`]},Vt(A,R=>R&&b("div",{class:`${t}-tabs-nav__prefix`},R)),U?b(Nt,{onResize:this.handleSegmentResize},{default:()=>b("div",{class:`${t}-tabs-rail`,ref:"tabsElRef"},b("div",{class:`${t}-tabs-capsule`,ref:"segmentCapsuleElRef"},b("div",{class:`${t}-tabs-wrapper`},b("div",{class:`${t}-tabs-tab`}))),F?N.map((R,w)=>(h.value.push(R.props.name),b(Wt,Object.assign({},R.props,{internalCreatedByPane:!0,internalLeftPadded:w!==0}),R.children?{default:R.children.tab}:void 0))):B.map((R,w)=>(h.value.push(R.props.name),w===0?R:oa(R))))}):b(Nt,{onResize:this.handleNavResize},{default:()=>b("div",{class:`${t}-tabs-nav-scroll-wrapper`,ref:"scrollWrapperElRef"},["top","bottom"].includes(Y)?b(wn,{ref:"xScrollInstRef",onScroll:this.handleScroll},{default:re}):b("div",{class:`${t}-tabs-nav-y-scroll`,onScroll:this.handleScroll,ref:"yScrollElRef"},re()))}),u&&p&&j?ra(p,!0):null,Vt(q,R=>R&&b("div",{class:`${t}-tabs-nav__suffix`},R))),F&&(this.animated&&(Y==="top"||Y==="bottom")?b("div",{ref:"tabsPaneWrapperRef",style:P,class:[`${t}-tabs-pane-wrapper`,_]},sa(N,this.mergedValue,this.renderedNames,this.onAnimationBeforeLeave,this.onAnimationEnter,this.onAnimationAfterEnter,this.animationDirection)):sa(N,this.mergedValue,this.renderedNames)))}});function sa(t,e,r,u,p,d,h){const m=[];return t.forEach(_=>{const{name:P,displayDirective:z,"display-directive":A}=_.props,q=B=>z===B||A===B,N=e===P;if(_.key!==void 0&&(_.key=P),N||q("show")||q("show:lazy")&&r.has(P)){r.has(P)||r.add(P);const B=!q("if");m.push(B?Oa(_,[[Ba,N]]):_)}}),h?b(Fa,{name:`${h}-transition`,onBeforeLeave:u,onEnter:p,onAfterEnter:d},{default:()=>m}):m}function ra(t,e){return b(Wt,{ref:"addTabInstRef",key:"__addable",name:"__addable",internalCreatedByPane:!0,internalAddable:!0,internalLeftPadded:e,disabled:typeof t=="object"&&t.disabled})}function oa(t){const e=Ha(t);return e.props?e.props.internalLeftPadded=!0:e.props={internalLeftPadded:!0},e}function Ft(t){return Array.isArray(t.dynamicProps)?t.dynamicProps.includes("internalLeftPadded")||t.dynamicProps.push("internalLeftPadded"):t.dynamicProps=["internalLeftPadded"],t}function Kn(t){return ee("GET","/api/keys",{signal:t})}function Dn(t){return ee("POST","/api/keys",{body:t})}function Vn(t){return ee("GET","/api/keys/"+t+"/view")}function Gn(t){return ee("GET","/api/keys/"+t+"/usage")}function Xn(t,e={}){return ee("GET","/api/keys/"+t+"/ips",{query:{days:e.days??1,sort:e.sort??"requests",direction:e.direction??"desc",limit:e.limit??100,offset:e.offset??0}})}function Ht(t,e){return ee("PATCH","/api/keys/"+t,{body:e})}function Qn(t){return ee("DELETE","/api/keys/"+t)}function Jn(){return ee("GET","/api/credits/selection-policy")}function Yn(){return ee("GET","/api/app/network")}function Zn(){return ee("GET","/api/models/aggregate")}function es(){return ee("GET","/api/groups")}function ts(t={}){return ee("GET","/api/accounts",{query:{all_platforms:1,page:t.page??1,page_size:t.pageSize??50,search:t.search||""}})}function as(){return ee("GET","/api/platforms")}function ns(t){return(Math.round(t*1e3)/10).toFixed(1)+"%"}function ss(t){const e=!!t.unlimited,r=Number(t.credit_limit||0),u=Number(t.credit_used||0),p=e||r<=0?0:Math.min(1,Math.max(0,u/r)),d=e?"ok":p>=.9?"danger":p>=.6?"warn":"ok",h=t.ip_summary||{days:1,unique_ips:0,requests:0,active_ips:0,active_requests:0},m=t.max_concurrent??null,_=t.rate_per_minute??null,P=Array.isArray(t.account_ids)?t.account_ids.map(Number).filter(A=>A>0):[],z=t.masked||(t.key_prefix?kn(t.key_prefix):He);return{id:t.id,name:t.name||"未命名",masked:z,keyPrefix:t.key_prefix||"",limitText:e?"∞":Pe(r),usedText:Pe(u),usageRatio:p,usageTone:d,usageText:e?"不限额度":ns(p),unlimited:e,concurrencyText:m===0?"不限":m==null?"全局":String(m),rateText:_===0?"不限频率":_==null?"频率随全局":_+" 次/分",ipText:(Number(h.unique_ips)||0)+" 个 / 24h",ipBadges:(h.top_ips||[]).slice(0,3).map(A=>({ip:A.ip,text:A.ip+" · "+(Number(A.active_requests)||0)+" 并发 · "+(Number(A.requests)||0)+" 次"})),groupLabel:t.group_name||"全部模型",statusLabel:t.status==="active"?"正常":"已停用",statusTone:t.status==="active"?"ok":"neutral",accountLabel:P.length?"独占 "+P.length+" 个账号":t.account_id?"独占账号："+(t.account_name||"#"+t.account_id):"公共账号池",expireText:He,note:t.note||"",platform:t.platform||"",aggregate:!!t.aggregate,groupId:Number(t.group_id||0),createdAt:t.created_at||"",status:t.status,creditLimit:r,creditUsed:u,maxConcurrent:m,ratePerMinute:_,accountIds:P,accountId:t.account_id,accountName:t.account_name||"",ipSummary:h}}function rs(t){return Array.isArray(t)?t.map(ss):[]}function os(t,e){const r=String(e.q||"").trim().toLowerCase(),u=String(e.group||""),p=String(e.status||"");return t.filter(d=>!(r&&![d.name,d.note,d.accountName,d.platform].join(" ").toLowerCase().includes(r)||u==="none"&&d.groupId!==0||u&&u!=="none"&&String(d.groupId)!==u||p&&d.status!==p))}function ls(t,e,r){if(!e||r!=="asc"&&r!=="desc")return t;const u=r==="asc"?1:-1,p=d=>e==="name"?0:e==="limit"?d.unlimited?Number.MAX_SAFE_INTEGER:d.creditLimit:e==="used"?d.creditUsed:e==="concurrency"?d.maxConcurrent==null?-1:d.maxConcurrent:e==="created"&&Date.parse(d.createdAt||"")||0;return[...t].sort((d,h)=>e==="name"?d.name.localeCompare(h.name,"zh-CN")*u:(p(d)-p(h))*u)}function is(t,e,r){const u=Math.max(1,Math.floor(r||20)),p=t.length,d=Math.max(1,Math.ceil(p/u)),h=Math.min(Math.max(1,Math.floor(e||1)),d);return{items:t.slice((h-1)*u,h*u),total:p,page:h,pageCount:d}}function la(t){return{total:t.length,active:t.filter(e=>e.status==="active").length,revoked:t.filter(e=>e.status!=="active").length,unlimited:t.filter(e=>e.unlimited).length}}function us(t){return{status:t==="active"?"revoked":"active"}}function qt(t,e){const r=String(t||""),u=String(e||"");return!r||!u||r.toLowerCase().endsWith("-"+u.toLowerCase())?r:r+"-"+u}function ds(t,e=12){const r=(t?.items||[]).filter(p=>(p.platforms||[]).some(d=>d.enabled!==!1)),u=r.slice(0,Math.max(0,e)).map(p=>{const d=(p.platforms||[]).filter(_=>_.enabled!==!1),h=d[0],m=(d.length?d.map(_=>{const P=_.client_id||qt(_.model_id,_.platform),z=_.multiplier==null?He:Number(_.multiplier)===0?"FREE":"×"+_.multiplier;return(_.platform_label||_.platform)+" · "+P+" · "+z}).join(`
`):"暂无平台明细")+`
点击复制调用名（带平台后缀）`;return{key:p.key||String(p.name),name:p.name,platformCount:d.length||Number(p.platform_count||0),copyValue:h?h.client_id||qt(h.model_id,h.platform):"",title:m}});return{chips:u,total:r.length,rest:Math.max(0,r.length-u.length)}}function cs(t,e){const r=String(e||"").trim().toLowerCase();return(t?.items||[]).filter(u=>(u.platforms||[]).some(p=>p.enabled!==!1)).map(u=>{const p=(u.platforms||[]).filter(d=>d.enabled!==!1);return{key:u.key||String(u.name),name:u.name,platformCount:p.length,rows:p.map(d=>({platform:d.platform,platformLabel:d.platform_label||d.platform,callId:d.client_id||qt(d.model_id,d.platform),upstreamId:d.model_id,multiplierText:d.multiplier==null?He:Number(d.multiplier)===0?"FREE":"×"+d.multiplier}))}}).filter(u=>!r||u.name.toLowerCase().includes(r)?!0:u.rows.some(p=>p.callId.toLowerCase().includes(r)||p.upstreamId.toLowerCase().includes(r)))}function ps(t,e){const r=new Map;e.origin&&r.set(e.origin,"当前访问 · "+(e.origin.replace(/^https?:\/\//,"")||"当前域名"));const u=["localhost","127.0.0.1"].includes(e.hostname),p=e.port||(u&&t?.port?String(t.port):""),d=p?":"+p:"";r.set("http://127.0.0.1"+d,"本机 · 127.0.0.1"+d);for(const h of t?.lan_ips||[])r.set("http://"+h+d,"局域网 · "+h+d);return[...r].map(([h,m])=>({value:h,label:m}))}function fs(t,e){const r=(t||"").replace(/\/+$/,"")+"/v1",u=(t||"").replace(/\/+$/,""),p=e||"auto";return{openai:["curl "+r+"/chat/completions \\",'  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"`+p+`","messages":[{"role":"user","content":"你好"}]}'`].join(`
`),claude:["curl "+u+"/v1/messages \\",'  -H "x-api-key: <你的 Key>" \\','  -H "anthropic-version: 2023-06-01" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'`].join(`
`),audioCurl:['curl "'+r+'/audio/transcriptions" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -F "file=@recording.webm" \\','  -F "model=workbuddy-asr" \\','  -F "response_format=json"'].join(`
`),audioPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','with open("recording.webm", "rb") as audio:','    result = client.audio.transcriptions.create(model="workbuddy-asr", file=audio)',"print(result.text)"].join(`
`),imagesPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','result = client.images.generate(model="hunyuan-image-alpha", prompt="白色背景上的蓝色圆形", n=1)',"print(result.data[0].url)",'with open("input.png", "rb") as image:','    edited = client.images.edit(model="hunyuan-image-alpha", image=image, prompt="把圆形改成绿色")',"print(edited.data[0].url)"].join(`
`),videoCurl:["# 1) 创建任务：立即返回 queued，不阻塞等待生成",'curl "'+r+'/videos" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"kling-video-v3","prompt":"一只猫在弹钢琴","seconds":"4","size":"1280x720"}'`,"","# 2) 轮询（用上一步返回的 id），直到 status=completed",'curl "'+r+'/videos/<视频 ID>" -H "Authorization: Bearer <你的 Key>"',"","# 3) 下载 MP4",'curl "'+r+'/videos/<视频 ID>/content" -H "Authorization: Bearer <你的 Key>" -o output.mp4'].join(`
`),videoPython:["import time","from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")',"",'video = client.videos.create(model="kling-video-v3", prompt="一只猫在弹钢琴", seconds="4", size="1280x720")',"print(video.id, video.status)          # queued","",'while video.status not in ("completed", "failed"):',"    time.sleep(5)","    video = client.videos.retrieve(video.id)","",'if video.status == "completed":',"    content = client.videos.download_content(video.id)",'    content.write_to_file("output.mp4")'].join(`
`)}}function ia(t,e){const r=String(t||"").trim();if(!r)return;const u=Number(r);if(!Number.isFinite(u))return e+"必须是数字";if(u<0)return e+"不能小于 0";if(!Number.isInteger(u))return e+"必须是整数"}function ms(t,e){const r={};if(e&&!String(t.name||"").trim()&&(r.name="名称不能为空"),!t.unlimited){const u=String(t.creditLimit||"").trim();if(u){const p=Number(u);Number.isFinite(p)?p<0&&(r.creditLimit="额度不能小于 0"):r.creditLimit="额度必须是数字"}}return r.maxConcurrent=ia(t.maxConcurrent,"并发上限"),r.ratePerMinute=ia(t.ratePerMinute,"每分钟请求数"),r}function bs(t){return Object.values(t).some(Boolean)}function vs(t){const e=u=>{const p=String(u||"").trim();return p===""?null:Number(p)},r=String(t.creditLimit||"").trim();return{name:String(t.name||"").trim(),note:String(t.note||""),group_id:Number(t.groupId||0),unlimited:t.unlimited||r==="",credit_limit:r===""?null:Number(r),max_concurrent:e(t.maxConcurrent),rate_per_minute:e(t.ratePerMinute),aggregate:!!t.aggregate,platform:String(t.platform||"").toLowerCase(),account_ids:t.accountIds.slice()}}function gs(t){const e=!!t.unlimited,r=t.usage_percent,u=e||r==null?0:Math.min(1,Math.max(0,r/100));return{limitText:e?"∞（无限制）":Pe(t.credit_limit),usedText:Pe(t.credit_used),remainingText:e||t.credit_remaining==null?"∞":Pe(t.credit_remaining),percentText:r==null?"N/A":r.toFixed(1)+"%",ratio:u,tone:e?"ok":u>=.9?"danger":u>=.6?"warn":"ok",unlimited:e,createdText:t.created_at?pa(t.created_at):He}}function ma(t){return{totalIps:Number(t.total_ips||0),requests:Number(t.total_requests||0),activeIps:Number(t.active_ips||0),activeRequests:Number(t.active_requests||0),rows:(t.items||[]).map(e=>({ip:e.ip,requests:Number(e.requests||0),failed:Number(e.failed||0),credits:Number(e.credits||0),tokens:Number(e.tokens||0),lastSeen:e.last_seen?pa(e.last_seen):He,models:(e.models||[]).map(r=>r.model).filter(Boolean).join("、"),active:Number(e.active_requests||0),blocked:!!e.blocked}))}}function Mt(){return ma({total_ips:0,total_requests:0,active_ips:0,active_requests:0,items:[]})}const ys={class:"keys-page"},hs={class:"keys-policy"},ks={class:"keys-share__grid"},xs={class:"keys-share__row"},_s={class:"keys-share__label"},ws={class:"keys-share__row"},Cs={class:"keys-share__label"},Ss={class:"keys-share__row"},Ts={class:"keys-share__label"},Rs={class:"keys-share__row"},Ps={class:"keys-share__label"},zs={class:"keys-share__row keys-share__row--models"},$s={class:"keys-share__label"},As={class:"keys-share__models"},Ls={class:"keys-muted"},Is={key:0,class:"keys-chip-count"},Es={key:1,class:"keys-muted"},Ns={class:"keys-filter"},Os={class:"keys-body"},Bs=["aria-label"],Fs={class:"keys-form"},Hs={class:"keys-form"},Ms={class:"keys-form__inline"},Ws={class:"keys-form"},qs={class:"keys-hint"},js={class:"keys-hint"},Us={class:"keys-hint"},Ks={class:"keys-modal__footer"},Ds={key:1,class:"keys-skeleton"},Vs={class:"keys-usage-grid"},Gs={class:"keys-usage-cell"},Xs={class:"tnum"},Qs={class:"keys-usage-cell"},Js={class:"tnum"},Ys={class:"keys-usage-cell"},Zs={class:"tnum"},er={class:"keys-usage-cell"},tr={class:"tnum"},ar={class:"keys-hint"},nr={class:"keys-ipbar"},sr={class:"keys-hint"},rr={key:0,class:"keys-muted"},or={class:"keys-model__head"},lr={class:"keys-muted"},ir={class:"keys-model__rows"},ur={class:"keys-muted"},dr={class:"keys-muted tnum"},cr={class:"keys-hint"},pr={class:"keys-hint"},fr={class:"keys-hint"},mr=ft({__name:"KeysPage",setup(t){const{t:e}=nn(),r=T(()=>e("page.keys.help")),u=T(()=>e("page.keys.authText")),p=qa(),d=on(),h=yn(),m=hn("keys",{pageSize:20,filters:{q:"",group:"",status:""}}),_=k(null),P=k(!1),z=k(null);let A=null;const q=T(()=>rs(_.value)),N=T(()=>os(q.value,m.state.filters)),B=T(()=>ls(N.value,m.state.sortBy,m.state.sortOrder)),F=T(()=>is(B.value,m.state.page,m.state.pageSize)),j=T(()=>la(q.value)),U=T(()=>P.value&&_.value===null),M=T(()=>!!(m.state.filters.q||m.state.filters.group||m.state.filters.status)),re=T(()=>{const s=la(N.value);return M.value?e("page.keys.summary.filtered",{total:s.total,all:j.value.total,active:s.active}):e("page.keys.summary.all",{total:s.total,active:s.active,revoked:s.revoked,unlimited:s.unlimited})}),Y=T(()=>M.value?e("page.keys.empty.filtered"):e("page.keys.empty.none")),R=k(null),w=k(null),W=k(null),te=k(!1),oe={origin:typeof window<"u"?window.location.origin:"",hostname:typeof window<"u"?window.location.hostname:"",port:typeof window<"u"?window.location.port:""},Ve=T(()=>ps(w.value,oe)),be=k(oe.origin);De(Ve,s=>{s.length&&!s.some(n=>n.value===be.value)&&(be.value=s[0].value)});const Ge=T(()=>(be.value||"").replace(/\/+$/,"")+"/v1"),ze=T(()=>(be.value||"").replace(/\/+$/,"")),le=T(()=>ds(W.value,12)),$e=T(()=>le.value.chips[0]?.copyValue||""),se=T(()=>fs(be.value,$e.value)),ie=T(()=>(R.value?.modes||[]).find(n=>n.value===R.value?.mode)?.label||e("page.keys.policyFallback")),mt=T(()=>((R.value?.modes||[]).find(n=>n.value===R.value?.mode)?.description||e("page.keys.policyFallbackHelp"))+e("page.keys.policySuffix"));function Xe(){return{name:"",note:"",groupId:0,unlimited:!0,creditLimit:"",maxConcurrent:"",ratePerMinute:"",aggregate:!0,platform:"",accountIds:[],resetUsed:!1}}const ye=k(!1),X=k(null),$=k(Xe()),ue=k({}),Me=k(!1),Qe=k(""),_e=k(!1),ae=k([]),we=k([]),Ae=k([]),We=k(!1),Le=k(!1),K=k({groups:!1,platforms:!1,accounts:!1}),Je=T(()=>K.value.groups||K.value.platforms||K.value.accounts);let Ie=!1;const bt=T(()=>[{label:e("page.keys.groupOption.none"),value:0},...ae.value.map(s=>({label:s.name+(s.effective_count!=null?e("page.keys.groupOption.count",{count:s.effective_count}):""),value:s.id}))]),qe=T(()=>[{label:e("page.keys.platformOption.none"),value:""},...we.value.map(s=>({label:s.label,value:s.storage}))]),vt=T(()=>Ae.value.map(s=>({label:(s.name||s.uid||e("page.keys.account.fallback"))+" #"+s.id+(s.status&&s.status!=="active"?e("page.keys.account.disabled"):"")+(s.balance_remain!=null?e("page.keys.account.credits",{amount:Pe(s.balance_remain)}):""),value:s.id}))),gt=T(()=>X.value?e("page.keys.drawer.edit"):e("page.keys.drawer.create")),yt=T(()=>X.value?e("common.save"):e("page.keys.submit.create")),Ce=k(!1),de=k(!1),l=k(""),i=k(null),v=k(!1),C=k(!1),I=k(""),V=k("1"),Q=k(""),G=k(Mt());let ve=0;const Ee=k(!1),je=k(""),Ne=k(!1),Ye=k("openai"),ht=T(()=>[{key:"ip",title:e("page.keys.ipc.ip"),mobile:"title",width:150},{key:"requests",title:e("page.keys.ipc.requests"),mobile:"meta",width:100,numeric:!0,render:s=>xe(s.requests)},{key:"failed",title:e("page.keys.ipc.failed"),mobile:"meta",width:80,numeric:!0,render:s=>xe(s.failed)},{key:"credits",title:e("page.keys.ipc.credits"),mobile:"meta",width:90,numeric:!0,render:s=>Pe(s.credits)},{key:"tokens",title:"Token",mobile:"meta",width:100,numeric:!0,render:s=>xe(s.tokens)},{key:"lastSeen",title:e("page.keys.ipc.lastSeen"),mobile:"meta",width:150},{key:"models",title:e("page.keys.ipc.models"),mobile:"subtitle",minWidth:180},{key:"active",title:e("page.keys.ipc.active"),mobile:"meta",width:80,numeric:!0,render:s=>xe(s.active)},{key:"blocked",title:e("page.keys.ipc.status"),mobile:"meta",width:90,render:s=>b(it,{tone:s.blocked?"danger":"ok"},{default:()=>s.blocked?e("page.keys.ipc.blocked"):e("page.keys.ipc.ok")})}]),Ze=T(()=>cs(W.value,je.value)),kt=T(()=>G.value.rows);function xt(s){if(typeof s=="string")return s;try{return JSON.stringify(s,null,2)}catch{return String(s)}}async function ce(){A?.abort();const s=new AbortController;A=s,P.value=!0,z.value=null;try{const n=await Kn(s.signal);_.value=Array.isArray(n?.items)?n.items:[];const o=Math.max(1,Math.ceil(N.value.length/m.state.pageSize));m.state.page>o&&m.setPage(o)}catch(n){if(mn(n))return;z.value={message:Et(n),detail:n instanceof bn&&n.detail?xt(n.detail):""},d.error(Oe(_.value===null?e("page.keys.action.load"):e("page.keys.action.refreshKeys"),n))}finally{A===s&&(A=null,P.value=!1)}}async function et(){Jn().then(s=>{R.value=s}).catch(()=>{R.value=null}),Yn().then(s=>{w.value=s}).catch(()=>{w.value=null}),te.value=!0;try{W.value=await Zn()}catch{W.value=null}finally{te.value=!1}}async function tt(s){We.value=!0;try{const n=await ts({search:s,pageSize:50});return Ae.value=Array.isArray(n?.items)?n.items:n?.accounts||[],K.value.accounts=!1,!0}catch{return Ae.value=[],K.value.accounts=!0,!1}finally{We.value=!1}}async function _t(){try{const s=await es();ae.value=Array.isArray(s?.items)?s.items:[],K.value.groups=!1}catch{ae.value=[],K.value.groups=!0}}async function wt(){try{const s=await as(),n=new Map;for(const o of s?.items||[]){const L=String(o.storage_platform||o.id||"").toLowerCase();!L||n.has(L)||n.set(L,String(o.label||L))}we.value=[...n].map(([o,L])=>({storage:o,label:L})),K.value.platforms=!1}catch{we.value=[],K.value.platforms=!0}}async function he(){if(!(Ie||Le.value)){Ie=!0,Le.value=!0,K.value={groups:!1,platforms:!1,accounts:!1};try{await Promise.all([_t(),wt(),tt("")])}finally{Le.value=!1,Je.value&&(Ie=!1)}}}async function Ct(){Le.value||(Ie=!1,await he())}const Se=k(m.state.filters.q);De(()=>m.state.filters.q,s=>{Se.value=s});const St=T(()=>[{label:e("page.keys.filter.statusAll"),value:""},{label:e("page.keys.filter.active"),value:"active"},{label:e("page.keys.filter.revoked"),value:"revoked"}]),Tt=T(()=>[{label:e("page.keys.filter.groupAll"),value:""},{label:e("page.keys.filter.groupNone"),value:"none"},...ae.value.map(s=>({label:s.name,value:String(s.id)}))]),Rt=T(()=>{const s=m.state.filters,n=[];if(s.q&&n.push({key:"q",label:e("page.keys.filter.chipSearch"),value:s.q}),s.group){const o=s.group==="none"?e("page.keys.filter.groupNone"):ae.value.find(L=>String(L.id)===s.group)?.name||s.group;n.push({key:"group",label:e("page.keys.filter.chipGroup"),value:o})}return s.status&&n.push({key:"status",label:e("page.keys.filter.chipStatus"),value:s.status==="active"?e("page.keys.filter.active"):e("page.keys.filter.revoked")}),n}),at={name:"name",limit:"limit",used:"used",concurrency:"concurrency"};function Ue(s){return m.state.sortBy!==s?!1:m.state.sortOrder==="asc"?"ascend":m.state.sortOrder==="desc"?"descend":!1}function Pt(s){if(!s||!s.order||!at[String(s.columnKey)]){m.setSort("","");return}m.setSort(at[String(s.columnKey)],s.order==="ascend"?"asc":"desc")}function zt(s){const n={};for(const[o,L]of Object.entries(s))n[o]=String(L??"");m.setFilters(n)}function nt(s,n){m.setFilter(s,n==null?"":String(n))}function ba(s){s==="q"&&(Se.value=""),m.setFilter(s,"")}function Ut(){Se.value="",m.reset()}function va(s){const n=s;return b("div",{class:"keys-name"},[b("span",{class:"keys-name__main",title:n.name},n.name),b("span",{class:"keys-name__sub"},n.accountLabel)])}function ga(s){const n=s;return b("div",{class:"keys-key"},[b("code",{class:"mono keys-key__text",title:n.masked},n.masked),b(un,{icon:sn,label:e("page.keys.cell.copyKey"),onClick:()=>{Kt(n)}})])}function ya(s){const n=s;return b("div",{class:"keys-quota"},[b("div",{class:"keys-quota__line tnum"},[b("span",{},n.usedText),b("span",{class:"keys-quota__sep"}," / "),b("span",{},n.limitText)]),n.unlimited?b("span",{class:"keys-quota__hint"},e("page.keys.cell.unlimited")):b(Yt,{value:n.usageRatio*100,tone:n.usageTone,height:4,ariaLabel:e("page.keys.cell.meterAria")})])}function ha(s){const n=s;return b("div",{class:"keys-concurrency"},[b("span",{class:"tnum"},n.concurrencyText),b("span",{class:"keys-concurrency__rate"},n.rateText)])}function ka(s){const n=s;return b("div",{class:"keys-ips"},[b("button",{type:"button",class:"keys-ips__count",title:e("page.keys.cell.ipsTitle"),onClick:()=>{Aa(n)}},n.ipText),n.ipBadges.length?b("div",{class:"keys-ips__badges"},n.ipBadges.map(o=>b(it,{key:o.ip,tone:"brand",title:o.text},{default:()=>o.ip}))):null])}function xa(s){const n=s;return n.groupId?b(it,{tone:"brand",title:e("page.keys.cell.groupTitle")},{default:()=>n.groupLabel}):b("span",{class:"keys-muted"},n.groupLabel)}function _a(s){const n=s;return b("span",{class:"keys-muted",title:e("page.keys.cell.expireTitle")},n.expireText)}function wa(s){const n=s;return b(it,{tone:n.statusTone,dot:!0},{default:()=>n.statusLabel})}function Ca(s){const n=s;return b(fn,{actions:[{key:"edit",label:e("common.edit")},{key:"copy",label:e("page.keys.action.copy")},{key:"reset",label:e("page.keys.action.reset")},{key:"toggle",label:n.status==="active"?e("page.keys.action.disable"):e("page.keys.action.enable")},{key:"usage",label:e("page.keys.action.usage")},{key:"delete",label:e("common.delete"),danger:!0}],max:2,onSelect:o=>{za(n,o)}})}const Sa=T(()=>[{key:"name",title:e("page.keys.col.name"),mobile:"title",minWidth:180,sortable:!0,sortOrder:Ue("name"),render:va},{key:"key",title:"Key",mobile:"subtitle",minWidth:220,render:ga},{key:"quota",title:e("page.keys.col.quota"),mobile:"meta",minWidth:170,sortable:!0,sortOrder:Ue("used"),render:ya},{key:"concurrency",title:e("page.keys.col.concurrency"),mobile:"meta",width:120,sortable:!0,sortOrder:Ue("concurrency"),render:ha},{key:"ips",title:e("page.keys.col.ips"),mobile:"meta",minWidth:170,render:ka},{key:"group",title:e("page.keys.col.group"),mobile:"meta",width:130,render:xa},{key:"expire",title:e("page.keys.col.expire"),mobile:"meta",width:90,render:_a},{key:"status",title:e("page.keys.col.status"),mobile:"meta",width:96,render:wa},{key:"actions",title:e("page.keys.col.actions"),mobile:"actions",width:150,hideable:!1,fixed:"right",render:Ca}]);function Ta(s){const n=Array.isArray(s)?s:s===null||s===""?[]:[s];$.value.accountIds=n.map(Number).filter(o=>Number.isFinite(o)&&o>0)}async function Kt(s){try{const n=await Vn(s.id);await rn(n.key)?d.success(e("page.keys.toast.copied")):d.error(e("common.copyFailed"))}catch(n){d.error(Oe(e("page.keys.action.read"),n))}}function Dt(){X.value=null,$.value=Xe(),ue.value={},ye.value=!0,he()}function Ra(s){X.value=s,$.value={name:s.name,note:s.note,groupId:s.groupId,unlimited:s.unlimited,creditLimit:s.unlimited?"":String(s.creditLimit),maxConcurrent:s.maxConcurrent==null?"":String(s.maxConcurrent),ratePerMinute:s.ratePerMinute==null?"":String(s.ratePerMinute),aggregate:s.aggregate,platform:s.platform,accountIds:s.accountIds.slice(),resetUsed:!1},ue.value={},ye.value=!0,he()}async function Pa(){if(ue.value=ms($.value,!!X.value),!bs(ue.value)){Me.value=!0;try{const s=vs($.value);if(X.value)await Ht(X.value.id,{...s,reset_used:$.value.resetUsed}),d.success(e("page.keys.toast.saved")),ye.value=!1,await ce();else{const n=await Dn({...s,credit_limit:s.credit_limit??null});ye.value=!1,Qe.value=n.key,_e.value=!0,await ce()}}catch(s){d.error(Oe(X.value?e("page.keys.action.saveKey"):e("page.keys.action.create"),s))}finally{Me.value=!1}}}async function za(s,n){if(n==="edit")return Ra(s);if(n==="copy")return Kt(s);if(n==="usage")return $a(s);if(n==="reset"){if(!await h({title:e("page.keys.confirmReset.title"),message:e("page.keys.confirmReset.body"),okText:e("page.keys.action.reset")}))return;try{await Ht(s.id,{reset_used:!0}),d.success(e("page.keys.toast.resetDone")),await ce()}catch(L){d.error(Oe(e("page.keys.action.resetUsed"),L))}return}if(n==="toggle"){try{await Ht(s.id,us(s.status)),d.success(s.status==="active"?e("page.keys.toast.disabled"):e("page.keys.toast.enabled")),await ce()}catch(o){d.error(Oe(s.status==="active"?e("page.keys.action.disableKey"):e("page.keys.action.enableKey"),o))}return}if(n==="delete"){if(!await h({title:e("page.keys.confirmDelete.title"),message:e("page.keys.confirmDelete.body"),danger:!0,okText:e("common.delete")}))return;try{await Qn(s.id),d.success(e("page.keys.toast.deleted")),await ce()}catch(L){d.error(Oe(e("page.keys.action.deleteKey"),L))}}}async function $a(s){Ce.value=!0,de.value=!0,l.value="",i.value=null;try{i.value=await Gn(s.id)}catch(n){l.value=Et(n)}finally{de.value=!1}}async function $t(){if(ve){C.value=!0,I.value="";try{const s=await Xn(ve,{days:Number(V.value)||1});G.value=ma(s)}catch(s){I.value=Et(s),G.value=Mt()}finally{C.value=!1}}}async function Aa(s){ve=s.id,Q.value=s.name,V.value="1",G.value=Mt(),I.value="",v.value=!0,await $t()}const La=T(()=>[{label:e("page.keys.ips.range24"),value:"1"},{label:e("page.keys.ips.range7"),value:"7"},{label:e("page.keys.ips.range30"),value:"30"}]),Ia=T(()=>[{label:e("page.keys.ipSummary.ips"),value:xe(G.value.totalIps)},{label:e("page.keys.ipc.requests"),value:xe(G.value.requests)},{label:e("page.keys.ipSummary.activeIps"),value:xe(G.value.activeIps)},{label:e("page.keys.ipSummary.activeRequests"),value:xe(G.value.activeRequests)}]),ge=T(()=>i.value?gs(i.value):null);return da(()=>{ce(),et()}),Wa(()=>{A?.abort()}),(s,n)=>(E(),Z("div",ys,[c(tn,{title:"API Keys",help:r.value},{actions:g(()=>[c(a(J),{variant:"quiet",icon:a(xn),loading:P.value,onClick:n[0]||(n[0]=o=>ce())},{default:g(()=>[H(x(a(e)("common.refresh")),1)]),_:1},8,["icon","loading"]),y("span",hs,[c(a(J),{variant:"quiet",onClick:n[1]||(n[1]=o=>a(p).push("/credits"))},{default:g(()=>[H(x(ie.value),1)]),_:1}),c(a(Gt),{content:mt.value,label:a(e)("page.keys.policyHelpLabel")},null,8,["content","label"])]),c(a(J),{variant:"default",onClick:n[2]||(n[2]=o=>Ne.value=!0)},{default:g(()=>[H(x(a(e)("page.keys.action.guide")),1)]),_:1}),c(a(J),{variant:"primary",icon:a(Zt),onClick:Dt},{default:g(()=>[H(x(a(e)("page.keys.action.create")),1)]),_:1},8,["icon"])]),_:1},8,["help"]),c(a(Xt),{class:"keys-share",title:a(e)("page.keys.share.title")},{extra:g(()=>[c(a(J),{variant:"quiet",size:"sm",loading:te.value,onClick:n[3]||(n[3]=o=>et())},{default:g(()=>[H(x(a(e)("common.refresh")),1)]),_:1},8,["loading"])]),default:g(()=>[y("div",ks,[y("div",xs,[y("span",_s,x(a(e)("page.keys.share.address")),1),c(a(Be),{class:"keys-share__select",value:be.value,options:Ve.value,"onUpdate:value":n[4]||(n[4]=o=>be.value=o)},null,8,["value","options"])]),y("div",ws,[y("span",Cs,[n[35]||(n[35]=H("OpenAI",-1)),y("em",null,x(a(e)("page.keys.share.openaiHint")),1)]),c(a(ut),{value:Ge.value},null,8,["value"])]),y("div",Ss,[y("span",Ts,[n[36]||(n[36]=H("Claude",-1)),y("em",null,x(a(e)("page.keys.share.claudeHint")),1)]),c(a(ut),{value:ze.value},null,8,["value"])]),y("div",Rs,[y("span",Ps,x(a(e)("page.keys.share.auth")),1),c(a(ut),{value:a(e)("page.keys.share.authValue")},null,8,["value"]),c(a(Gt),{content:u.value,label:a(e)("page.keys.share.authHelpLabel")},null,8,["content","label"])]),y("div",zs,[y("span",$s,x(a(e)("page.keys.share.models")),1),y("div",As,[y("span",Ls,x(a(e)("page.keys.share.groups",{count:le.value.total})),1),(E(!0),Z(Fe,null,st(le.value.chips,o=>(E(),ne(a(Qt),{key:o.key,mono:"",copy:"",value:o.copyValue,title:o.title},{default:g(()=>[H(x(o.name),1),o.platformCount>1?(E(),Z("span",Is,x(o.platformCount),1)):ke("",!0)]),_:2},1032,["value","title"]))),128)),le.value.total?(E(),ne(a(J),{key:0,variant:"quiet",size:"sm",onClick:n[5]||(n[5]=o=>Ee.value=!0)},{default:g(()=>[H(x(a(e)("page.keys.share.allModels",{count:le.value.total})),1)]),_:1})):ke("",!0),!le.value.total&&!te.value?(E(),Z("span",Es,x(a(e)("page.keys.share.noWhitelist")),1)):ke("",!0)])])])]),_:1},8,["title"]),c(a(Xt),{flush:"",class:"keys-card"},{default:g(()=>[y("div",Ns,[c(a(pn),{chips:Rt.value,summary:re.value,loading:P.value,onChange:zt,onReset:Ut,onRemove:ba},{default:g(({change:o})=>[c(a(Te),{class:"keys-filter__search",value:Se.value,placeholder:a(e)("page.keys.filter.searchPlaceholder"),clearable:"","onUpdate:value":L=>{Se.value=L,o("q",L)}},null,8,["value","placeholder","onUpdate:value"]),c(a(Be),{class:"keys-filter__select",value:a(m).state.filters.group,options:Tt.value,"onUpdate:value":n[6]||(n[6]=L=>nt("group",L))},null,8,["value","options"]),c(a(Be),{class:"keys-filter__select",value:a(m).state.filters.status,options:St.value,"onUpdate:value":n[7]||(n[7]=L=>nt("status",L))},null,8,["value","options"])]),_:1},8,["chips","summary","loading"])]),y("div",Os,[z.value&&_.value===null?(E(),ne(a(ot),{key:0,title:a(e)("page.keys.error.load",{error:z.value.message}),detail:z.value.detail,onRetry:n[8]||(n[8]=o=>ce())},null,8,["title","detail"])):U.value?(E(),Z("div",{key:1,class:"keys-skeleton","aria-busy":"true","aria-label":a(e)("page.keys.loadingAria")},[(E(),Z(Fe,null,st(6,o=>c(a(ta),{key:o,height:"44px"})),64))],8,Bs)):(E(),ne(a(Jt),{key:2,"table-id":"keys",columns:Sa.value,data:F.value.items,"row-key":"id",remote:"",loading:P.value,page:a(m).state.page,"page-size":a(m).state.pageSize,"item-count":F.value.total,"page-sizes":[20,50,100],"empty-description":Y.value,"onUpdate:page":a(m).setPage,"onUpdate:pageSize":a(m).setPageSize,"onUpdate:sorter":Pt},{empty:g(()=>[c(a(ln),{description:Y.value},{default:g(()=>[M.value?(E(),ne(a(J),{key:0,variant:"default",size:"sm",onClick:Ut},{default:g(()=>[H(x(a(e)("page.keys.clearFilters")),1)]),_:1})):(E(),ne(a(J),{key:1,variant:"primary",size:"sm",icon:a(Zt),onClick:Dt},{default:g(()=>[H(x(a(e)("page.keys.action.create")),1)]),_:1},8,["icon"]))]),_:1},8,["description"])]),_:1},8,["columns","data","loading","page","page-size","item-count","empty-description","onUpdate:page","onUpdate:pageSize"]))])]),_:1}),c(a(gn),{show:ye.value,"onUpdate:show":n[20]||(n[20]=o=>ye.value=o),title:gt.value,size:"lg","submit-text":yt.value,"submit-loading":Me.value,onSubmit:Pa},{default:g(()=>[Je.value?(E(),ne(a(ot),{key:0,compact:"",title:a(e)("page.keys.form.auxFailed"),"retry-label":a(e)("page.keys.form.reload"),onRetry:Ct},null,8,["title","retry-label"])):ke("",!0),c(a(lt),{title:a(e)("page.keys.section.basic"),first:""},{default:g(()=>[y("div",Fs,[c(a(me),{label:a(e)("page.keys.form.name"),required:!!X.value,error:ue.value.name,hint:a(e)("page.keys.form.nameHint")},{default:g(()=>[c(a(Te),{value:$.value.name,"onUpdate:value":n[9]||(n[9]=o=>$.value.name=o),placeholder:a(e)("page.keys.form.namePlaceholder"),maxlength:"120",clearable:""},null,8,["value","placeholder"])]),_:1},8,["label","required","error","hint"]),c(a(me),{label:a(e)("page.keys.form.group"),hint:a(e)("page.keys.form.groupHint")},{default:g(()=>[c(a(Be),{value:$.value.groupId,"onUpdate:value":n[10]||(n[10]=o=>$.value.groupId=o),options:bt.value},null,8,["value","options"])]),_:1},8,["label","hint"]),c(a(me),{label:a(e)("page.keys.form.note")},{default:g(()=>[c(a(Te),{value:$.value.note,"onUpdate:value":n[11]||(n[11]=o=>$.value.note=o),type:"textarea",rows:2,maxlength:"255",placeholder:a(e)("page.keys.form.notePlaceholder")},null,8,["value","placeholder"])]),_:1},8,["label"])])]),_:1},8,["title"]),c(a(lt),{title:a(e)("page.keys.section.quota")},{default:g(()=>[y("div",Hs,[c(a(me),{label:a(e)("page.keys.form.limit"),error:ue.value.creditLimit,hint:a(e)("page.keys.form.limitHint")},{default:g(()=>[y("div",Ms,[c(a(It),{checked:$.value.unlimited,"onUpdate:checked":n[12]||(n[12]=o=>$.value.unlimited=o)},{default:g(()=>[H(x(a(e)("page.keys.form.unlimited")),1)]),_:1},8,["checked"]),c(a(Te),{value:$.value.creditLimit,"onUpdate:value":n[13]||(n[13]=o=>$.value.creditLimit=o),placeholder:a(e)("page.keys.form.limitPlaceholder"),disabled:$.value.unlimited},null,8,["value","placeholder","disabled"])])]),_:1},8,["label","error","hint"]),c(a(me),{label:a(e)("page.keys.form.concurrency"),error:ue.value.maxConcurrent,hint:a(e)("page.keys.form.concurrencyHint")},{default:g(()=>[c(a(Te),{value:$.value.maxConcurrent,"onUpdate:value":n[14]||(n[14]=o=>$.value.maxConcurrent=o),placeholder:a(e)("page.keys.form.concurrencyPlaceholder")},null,8,["value","placeholder"])]),_:1},8,["label","error","hint"]),c(a(me),{label:a(e)("page.keys.form.rate"),error:ue.value.ratePerMinute,hint:a(e)("page.keys.form.rateHint")},{default:g(()=>[c(a(Te),{value:$.value.ratePerMinute,"onUpdate:value":n[15]||(n[15]=o=>$.value.ratePerMinute=o),placeholder:a(e)("page.keys.form.concurrencyPlaceholder")},null,8,["value","placeholder"])]),_:1},8,["label","error","hint"]),X.value?(E(),ne(a(me),{key:0,label:a(e)("page.keys.form.resetUsed")},{default:g(()=>[c(a(It),{checked:$.value.resetUsed,"onUpdate:checked":n[16]||(n[16]=o=>$.value.resetUsed=o)},{default:g(()=>[H(x(a(e)("page.keys.form.resetUsedLabel",{used:X.value?.usedText||""})),1)]),_:1},8,["checked"])]),_:1},8,["label"])):ke("",!0)])]),_:1},8,["title"]),c(a(lt),{title:a(e)("page.keys.section.scope")},{default:g(()=>[y("div",Ws,[c(a(me),{label:a(e)("page.keys.form.aggregate"),hint:a(e)("page.keys.form.aggregateHint")},{default:g(()=>[c(a(It),{checked:$.value.aggregate,"onUpdate:checked":n[17]||(n[17]=o=>$.value.aggregate=o)},{default:g(()=>[H(x(a(e)("page.keys.form.aggregateLabel")),1)]),_:1},8,["checked"])]),_:1},8,["label","hint"]),c(a(me),{label:a(e)("page.keys.form.platform"),hint:a(e)("page.keys.form.platformHint")},{default:g(()=>[c(a(Be),{value:$.value.platform,"onUpdate:value":n[18]||(n[18]=o=>$.value.platform=o),options:qe.value},null,8,["value","options"])]),_:1},8,["label","hint"]),c(a(me),{label:a(e)("page.keys.form.accounts"),hint:a(e)("page.keys.form.accountsHint")},{default:g(()=>[c(a(Be),{value:$.value.accountIds,multiple:"",filterable:"",remote:"",clearable:"",loading:We.value,options:vt.value,placeholder:a(e)("page.keys.form.accountsPlaceholder"),"onUpdate:value":Ta,onSearch:n[19]||(n[19]=o=>tt(o))},null,8,["value","loading","options","placeholder"])]),_:1},8,["label","hint"])])]),_:1},8,["title"]),c(a(lt),{title:a(e)("page.keys.section.access")},{default:g(()=>[y("p",qs,x(a(e)("page.keys.access.ipNote")),1),y("p",js,x(a(e)("page.keys.access.expireNote")),1)]),_:1},8,["title"])]),_:1},8,["show","title","submit-text","submit-loading"]),c(a(an),{show:_e.value,"onUpdate:show":n[22]||(n[22]=o=>_e.value=o),preset:"card",class:"keys-modal",title:a(e)("page.keys.created.title"),style:{width:"520px"}},{footer:g(()=>[y("div",Ks,[c(a(J),{variant:"primary",onClick:n[21]||(n[21]=o=>_e.value=!1)},{default:g(()=>[H(x(a(e)("page.keys.created.done")),1)]),_:1})])]),default:g(()=>[y("p",Us,x(a(e)("page.keys.created.note")),1),c(a(ut),{value:Qe.value},null,8,["value"])]),_:1},8,["show","title"]),c(a(dt),{show:Ce.value,"onUpdate:show":n[24]||(n[24]=o=>Ce.value=o),title:a(e)("page.keys.usage.title"),subtitle:a(e)("page.keys.usage.subtitle")},{footer:g(()=>[c(a(J),{variant:"default",onClick:n[23]||(n[23]=o=>Ce.value=!1)},{default:g(()=>[H(x(a(e)("common.close")),1)]),_:1})]),default:g(()=>[l.value?(E(),ne(a(ot),{key:0,title:a(e)("page.keys.usage.error",{error:l.value}),retry:!1,compact:""},null,8,["title"])):de.value?(E(),Z("div",Ds,[c(a(ta),{repeat:3,text:""})])):ge.value?(E(),Z(Fe,{key:2},[y("div",Vs,[y("div",Gs,[y("span",null,x(a(e)("page.keys.usage.limit")),1),y("strong",Xs,x(ge.value.limitText),1)]),y("div",Qs,[y("span",null,x(a(e)("page.keys.usage.used")),1),y("strong",Js,x(ge.value.usedText),1)]),y("div",Ys,[y("span",null,x(a(e)("page.keys.usage.remaining")),1),y("strong",Zs,x(ge.value.remainingText),1)]),y("div",er,[y("span",null,x(a(e)("page.keys.usage.rate")),1),y("strong",tr,x(ge.value.percentText),1)])]),ge.value.unlimited?ke("",!0):(E(),ne(a(Yt),{key:0,value:ge.value.ratio*100,tone:ge.value.tone,height:8,"aria-label":a(e)("page.keys.usage.meterAria")},null,8,["value","tone","aria-label"])),y("p",ar,x(a(e)("page.keys.usage.createdAt",{time:ge.value.createdText})),1)],64)):ke("",!0)]),_:1},8,["show","title","subtitle"]),c(a(dt),{show:v.value,"onUpdate:show":n[28]||(n[28]=o=>v.value=o),title:a(e)("page.keys.ips.title",{name:Q.value}),subtitle:a(e)("page.keys.ips.subtitle")},{footer:g(()=>[c(a(J),{variant:"default",onClick:n[27]||(n[27]=o=>v.value=!1)},{default:g(()=>[H(x(a(e)("common.close")),1)]),_:1})]),default:g(()=>[y("div",nr,[c(a(dn),{options:La.value,"model-value":V.value,"aria-label":a(e)("page.keys.ips.rangeAria"),"onUpdate:modelValue":n[25]||(n[25]=o=>{V.value=String(o),$t()})},null,8,["options","model-value","aria-label"]),c(a(J),{variant:"quiet",size:"sm",loading:C.value,onClick:n[26]||(n[26]=o=>$t())},{default:g(()=>[H(x(a(e)("common.refresh")),1)]),_:1},8,["loading"])]),c(a(cn),{items:Ia.value},null,8,["items"]),I.value?(E(),ne(a(ot),{key:0,title:a(e)("page.keys.ips.error",{error:I.value}),retry:!1,compact:""},null,8,["title"])):(E(),ne(a(Jt),{key:1,"table-id":"keys-ips",columns:ht.value,data:kt.value,"row-key":"ip",pagination:!1,"virtual-scroll":!1,"max-height":"360px","empty-description":a(e)("page.keys.ips.empty")},null,8,["columns","data","empty-description"])),y("p",sr,x(a(e)("page.keys.ips.note")),1)]),_:1},8,["show","title","subtitle"]),c(a(dt),{show:Ee.value,"onUpdate:show":n[31]||(n[31]=o=>Ee.value=o),title:a(e)("page.keys.models.title",{count:le.value.total}),subtitle:a(e)("page.keys.models.subtitle")},{footer:g(()=>[c(a(J),{variant:"default",onClick:n[30]||(n[30]=o=>Ee.value=!1)},{default:g(()=>[H(x(a(e)("common.close")),1)]),_:1})]),default:g(()=>[c(a(Te),{value:je.value,"onUpdate:value":n[29]||(n[29]=o=>je.value=o),placeholder:a(e)("page.keys.models.searchPlaceholder"),clearable:""},null,8,["value","placeholder"]),Ze.value.length?ke("",!0):(E(),Z("div",rr,x(a(e)("page.keys.models.noHits")),1)),(E(!0),Z(Fe,null,st(Ze.value,o=>(E(),Z("div",{key:o.key,class:"keys-model"},[y("div",or,[y("strong",null,x(o.name),1),y("span",lr,x(a(e)("page.keys.models.platformCount",{count:o.platformCount})),1)]),y("div",ir,[(E(!0),Z(Fe,null,st(o.rows,L=>(E(),Z("div",{key:L.callId,class:"keys-model__row"},[y("span",ur,x(L.platformLabel),1),c(a(Qt),{mono:"",copy:"",value:L.callId,title:a(e)("page.keys.models.upstreamId",{id:L.upstreamId})},{default:g(()=>[H(x(L.callId),1)]),_:2},1032,["value","title"]),y("span",dr,x(L.multiplierText),1)]))),128))])]))),128))]),_:1},8,["show","title","subtitle"]),c(a(dt),{show:Ne.value,"onUpdate:show":n[34]||(n[34]=o=>Ne.value=o),title:a(e)("page.keys.guide.title"),subtitle:a(e)("page.keys.guide.subtitle")},{footer:g(()=>[c(a(J),{variant:"default",onClick:n[33]||(n[33]=o=>Ne.value=!1)},{default:g(()=>[H(x(a(e)("common.close")),1)]),_:1})]),default:g(()=>[c(a(Un),{value:Ye.value,"onUpdate:value":n[32]||(n[32]=o=>Ye.value=o),type:"line",animated:""},{default:g(()=>[c(a(Ke),{name:"openai",tab:a(e)("page.keys.guide.openai")},{default:g(()=>[c(a(Re),{code:se.value.openai,language:"bash"},null,8,["code"])]),_:1},8,["tab"]),c(a(Ke),{name:"claude",tab:"Claude Code / Anthropic"},{default:g(()=>[c(a(Re),{code:se.value.claude,language:"bash"},null,8,["code"])]),_:1}),c(a(Ke),{name:"audio",tab:a(e)("page.keys.guide.audio")},{default:g(()=>[y("p",cr,x(a(e)("page.keys.guide.audioNote")),1),c(a(Re),{code:se.value.audioCurl,language:"bash"},null,8,["code"]),c(a(Re),{code:se.value.audioPython,language:"python"},null,8,["code"])]),_:1},8,["tab"]),c(a(Ke),{name:"images",tab:a(e)("page.keys.guide.images")},{default:g(()=>[y("p",pr,x(a(e)("page.keys.guide.imagesNote")),1),c(a(Re),{code:se.value.imagesPython,language:"python"},null,8,["code"])]),_:1},8,["tab"]),c(a(Ke),{name:"videos",tab:a(e)("page.keys.guide.videos")},{default:g(()=>[y("p",fr,x(a(e)("page.keys.guide.videosNote")),1),c(a(Re),{code:se.value.videoCurl,language:"bash"},null,8,["code"]),c(a(Re),{code:se.value.videoPython,language:"python"},null,8,["code"])]),_:1},8,["tab"])]),_:1},8,["value"])]),_:1},8,["show","title","subtitle"])]))}}),ao=vn(mr,[["__scopeId","data-v-e9f8ea49"]]);export{ao as default};
